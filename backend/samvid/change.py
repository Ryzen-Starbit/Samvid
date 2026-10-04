from __future__ import annotations
import math
import numpy as np
from scipy import ndimage as ndi
from sklearn.cluster import HDBSCAN
from . import config as cfg
from . import imagery as im
from . import seasonal
from .archive import load_stack

FAMILY = {
    "construction": "construction", "site_clearing": "construction", "clearance": "clearance",
    "water_expansion": "water", "water_recession": "water", "road_development": "road",
    "vehicle_concentration": "vehicle", "vehicle_dispersal": "vehicle", "demolition": "construction",
    "vegetation_regrowth": "vegetation", "unclassified": "other",
}
LABELS = {
    "construction": "Construction", "site_clearing": "Site clearing / earthworks", "clearance": "Vegetation clearance",
    "water_expansion": "Water-extent expansion", "water_recession": "Water-extent recession",
    "road_development": "Road / track development", "vehicle_concentration": "Vehicle concentration",
    "vehicle_dispersal": "Vehicle dispersal", "demolition": "Demolition", "vegetation_regrowth": "Vegetation regrowth",
    "unclassified": "Unclassified change",
}
NAIVE_SIGMA = seasonal.SIGMA_FLOOR * 1.0

def _anomaly(st, k):
    f = seasonal.feature_stack(st["norm"][k:k + 1].astype(np.float32))[0]
    return f - seasonal.expected(st["coef"], str(st["dates"][k]))

def _raw_feats(st, k):
    return seasonal.feature_stack(st["norm"][k:k + 1].astype(np.float32))[0]

def zmap(st, i, j, use_seasonal=True):
    if use_seasonal:
        d = _anomaly(st, j) - _anomaly(st, i)
        sf = np.maximum(seasonal.support_factor(st["coverage"], str(st["dates"][i])),
                        seasonal.support_factor(st["coverage"], str(st["dates"][j])))
        z = d / (math.sqrt(2) * st["sigma"] * sf)
    else:
        d = _raw_feats(st, j) - _raw_feats(st, i)
        z = d / (math.sqrt(2) * NAIVE_SIGMA[:, None, None])
    n = seasonal.CHANGE_FEATURES
    return d[:n], z[:n]

def _objects_hdbscan(changed, d):
    ys, xs = np.nonzero(changed)
    if ys.size < cfg.MIN_CHANGE_OBJECT_PX:
        return np.zeros(changed.shape, np.int32), {}
    sgn = np.sign(np.round(d[:, ys, xs] / 0.05)).T * 2.0       
    X = np.column_stack([xs / 3.0, ys / 3.0, sgn])
    if X.shape[0] > 20000:                                       
        keep = np.random.default_rng(0).choice(X.shape[0], 20000, replace=False)
        ys, xs, X = ys[keep], xs[keep], X[keep]
    hd = HDBSCAN(min_cluster_size=cfg.MIN_CHANGE_OBJECT_PX, min_samples=6, copy=True)
    lab = hd.fit_predict(X)
    out = np.zeros(changed.shape, np.int32)
    out[ys, xs] = lab + 1
    probs = {}
    for k in np.unique(lab):
        if k >= 0:
            probs[int(k) + 1] = float(hd.probabilities_[lab == k].mean())
    return out, probs

def _classify(before, after, pix, elong, dmean=None):
    def frac(cls, c):
        v = cls[pix]
        v = v[v != im.MASKED]
        return float((v == c).mean()) if v.size else 0.0
    b = {c: frac(before, c) for c in range(7)}
    a = {c: frac(after, c) for c in range(7)}
    g = {c: a[c] - b[c] for c in range(7)}
    if g[im.WATER] > 0.35:
        t = "water_expansion"
    elif g[im.WATER] < -0.35:
        t = "water_recession"
    elif g[im.VEHICLE] > 0.12:
        t = "vehicle_concentration"
    elif g[im.VEHICLE] < -0.12:
        t = "vehicle_dispersal"
    elif g[im.ROAD] > 0.25 and elong > 0.8:
        t = "road_development"
    elif g[im.BUILT] > 0.25:
        t = "construction"
    elif g[im.DISTURBED] > 0.35:
        t = "site_clearing"
    elif g[im.VEG] < -0.35 or (dmean is not None and dmean[0] < -0.15 and a[im.WATER] < 0.3
                                 and (a[im.BARE] + a[im.DISTURBED]) > 0.5):
        t = "clearance"
    elif g[im.BUILT] < -0.35:
        t = "demolition"
    elif g[im.VEG] > 0.35 or (dmean is not None and dmean[0] > 0.2 and a[im.VEG] > 0.5):
        t = "vegetation_regrowth"
    else:
        t = "unclassified"
    return t, b, a

DOMINANT = {"construction": [im.BUILT], "site_clearing": [im.DISTURBED, im.BUILT], "clearance": [im.BARE, im.DISTURBED],
            "water_expansion": [im.WATER], "water_recession": [im.WATER], "road_development": [im.ROAD],
            "vehicle_concentration": [im.VEHICLE], "vehicle_dispersal": [im.VEHICLE], "demolition": [im.BUILT],
            "vegetation_regrowth": [im.VEG], "unclassified": [im.BUILT]}

def _direction(t, before, after, bbox):
    x0, y0, x1, y1 = bbox
    pad = 6
    sl = (slice(max(0, y0 - pad), y1 + pad), slice(max(0, x0 - pad), x1 + pad))
    cls_ = DOMINANT[t]
    nb = int(np.isin(before[sl], cls_).sum())
    na = int(np.isin(after[sl], cls_).sum())
    if nb <= 0.05 * max(na, 1):
        d = "appearance"
    elif na <= 0.05 * max(nb, 1):
        d = "disappearance"
    elif na > nb:
        d = "expansion"
    else:
        d = "contraction"
    return d, nb, na

def _nearest_sar(st, date, max_days=40):
    from datetime import date as _d
    if len(st["sar_dates"]) == 0:
        return None
    t0 = _d.fromisoformat(date)
    best = min(range(len(st["sar_dates"])), key=lambda k: abs((_d.fromisoformat(str(st["sar_dates"][k])) - t0).days))
    if abs((_d.fromisoformat(str(st["sar_dates"][best])) - t0).days) > max_days:
        return None
    return best

def sar_corroboration(st, pix, date_a, date_b):
    ka, kb = _nearest_sar(st, date_a), _nearest_sar(st, date_b)
    if ka is None or kb is None or ka == kb:
        return None
    a = ndi.uniform_filter(st["sar"][ka].astype(np.float32), 3)[pix]
    b = ndi.uniform_filter(st["sar"][kb].astype(np.float32), 3)[pix]
    delta = float(np.median(b - a))
    return {"sar_before": str(st["sar_ids"][ka]), "sar_after": str(st["sar_ids"][kb]),
            "median_delta_db": round(delta, 2), "corroborated": bool(abs(delta) > 2.5)}

def confidence(mag, area, qual, reg_err, prob, sar):
    s = 1.0 * (mag - cfg.CHANGE_Z_THRESHOLD) / 2.0 + 1.1 * math.log10(max(area, 1) / cfg.MIN_CHANGE_OBJECT_PX)
    c = 1 / (1 + math.exp(-s))
    c *= (0.55 + 0.45 * qual)
    c *= 1 - 0.4 * min(reg_err / 1.5, 1.0)
    c *= 0.75 + 0.25 * prob
    if sar is not None:
        c = c + (1 - c) * 0.35 if sar["corroborated"] else c * 0.85
    return round(float(min(c, 0.99)), 3)

def _merge_objects(lab, probs, before, after, d, gap=3):
    keys = list(probs)
    fam = {}
    for k in keys:
        pix = lab == k
        ys, xs = np.nonzero(pix)
        if ys.size < 3:
            fam[k] = "other"
            continue
        ev = np.linalg.eigvalsh(np.cov(np.stack([xs, ys])) + 1e-6 * np.eye(2))
        fam[k] = FAMILY[_classify(before, after, pix, float(1 - ev[0] / ev[1]), d[:, pix].mean(1))[0]]
    parent = {k: k for k in keys}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    dil = {k: ndi.binary_dilation(lab == k, iterations=gap * 3 if fam[k] == "vehicle" else gap) for k in keys}
    for a_i, a in enumerate(keys):
        for b in keys[a_i + 1:]:
            if fam[a] == fam[b] and fam[a] != "other" and (dil[a] & (lab == b)).any():
                parent[find(b)] = find(a)
    out = np.zeros_like(lab)
    newp = {}
    for k in keys:
        r = find(k)
        out[lab == k] = r
        n = int((lab == k).sum())
        tot, w = newp.get(r, (0.0, 0))
        newp[r] = (tot + probs[k] * n, w + n)
    return out, {r: t / max(w, 1) for r, (t, w) in newp.items()}

def detect_pair(aoi, i, j, use_seasonal=True, scene_reg=None):
    st = load_stack(aoi)
    d, z = zmap(st, i, j, use_seasonal)
    valid = st["valid"][i] & st["valid"][j]
    zmax = np.abs(z).max(0)
    changed = valid & (zmax > cfg.CHANGE_Z_THRESHOLD)
    result = {"pair": [str(st["sids"][i]), str(st["sids"][j])], "dates": [str(st["dates"][i]), str(st["dates"][j])],
              "valid_fraction": float(valid.mean()), "changed_px": int(changed.sum()), "objects": []}
    if not use_seasonal:
        # naive single-pair baseline: connected components, no HDBSCAN / seasonal model
        lab, n = ndi.label(ndi.binary_opening(changed))
        sizes = ndi.sum(changed, lab, range(1, n + 1)) if n else []
        result["objects"] = [{"label": k + 1, "area_px": int(s)} for k, s in enumerate(sizes) if s >= cfg.MIN_CHANGE_OBJECT_PX]
        result["label_map"] = lab
        result["changed_mask"] = changed
        return result
    lab, probs = _objects_hdbscan(changed, d)
    before, after = st["cls"][i], st["cls"][j]
    lab, probs = _merge_objects(lab, probs, before, after, d)
    for k, prob in probs.items():
        pix = lab == k
        n = int(pix.sum())
        if n < cfg.MIN_CHANGE_OBJECT_PX:
            continue
        pix = ndi.binary_closing(pix, iterations=1) & valid
        ys, xs = np.nonzero(pix)
        if ys.size < cfg.MIN_CHANGE_OBJECT_PX:
            continue
        bbox = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
        cov = np.cov(np.stack([xs, ys])) + 1e-6 * np.eye(2)
        ev = np.linalg.eigvalsh(cov)
        elong = float(1 - ev[0] / ev[1])
        t, fb, fa = _classify(before, after, pix, elong, d[:, pix].mean(1))
        direction, nb, na = _direction(t, before, after, bbox)
        mag = float(np.median(zmax[pix]))
        sl = (slice(bbox[1], bbox[3]), slice(bbox[0], bbox[2]))
        qual = float(min(st["valid"][i][sl].mean(), st["valid"][j][sl].mean()))
        reg_err = 0.0
        if scene_reg:
            reg_err = max(scene_reg.get(str(st["sids"][i]), 0), scene_reg.get(str(st["sids"][j]), 0))
        sar = sar_corroboration(st, pix, str(st["dates"][i]), str(st["dates"][j]))
        conf = confidence(mag, ys.size, qual, reg_err, prob, sar)
        f_obs_a = _raw_feats(st, i)[:, pix].mean(1)
        f_obs_b = _raw_feats(st, j)[:, pix].mean(1)
        f_exp_a = seasonal.expected(st["coef"], str(st["dates"][i]))[:, pix].mean(1)
        f_exp_b = seasonal.expected(st["coef"], str(st["dates"][j]))[:, pix].mean(1)
        sig = st["sigma"][:, pix].mean(1)
        result["objects"].append({
            "label": k, "mask": pix, "bbox": bbox, "area_px": int(ys.size), "type": t, "direction": direction,
            "class_before": {im.CLASS_NAMES[c]: round(v, 3) for c, v in fb.items()},
            "class_after": {im.CLASS_NAMES[c]: round(v, 3) for c, v in fa.items()},
            "dominant_area_before_px": nb, "dominant_area_after_px": na,
            "z_median": round(mag, 2), "hdbscan_prob": round(prob, 3), "quality": round(qual, 3),
            "registration_error_px": round(reg_err, 2), "elongation": round(elong, 3), "sar": sar,
            "confidence": conf,
            "seasonal": {
                "ndvi_before": {"observed": round(float(f_obs_a[4]), 3), "expected": round(float(f_exp_a[4]), 3)},
                "ndvi_after": {"observed": round(float(f_obs_b[4]), 3), "expected": round(float(f_exp_b[4]), 3)},
                "ndvi_sigma": round(float(sig[4]), 3),
                "raw_delta": {n_: round(float(v), 3) for n_, v in zip(seasonal.FEATURES, f_obs_b - f_obs_a)},
                "expected_seasonal_delta": {n_: round(float(v), 3) for n_, v in zip(seasonal.FEATURES, f_exp_b - f_exp_a)},
            },
        })
    result["label_map"] = lab
    result["changed_mask"] = changed
    return result

def earliest_observation(aoi, pix, i, j):
    st = load_stack(aoi)
    anom_i = _anomaly(st, i)
    sf_i = seasonal.support_factor(st["coverage"], str(st["dates"][i]))
    n_pix = max(1, int(pix.sum()))
    usable, skipped = [], []
    for k in range(i + 1, j + 1):
        vf = float((st["valid"][k] & pix).sum() / n_pix)
        (usable if vf >= 0.6 else skipped).append(k)
    if j not in usable:
        usable.append(j)
    def support(k):
        n = seasonal.CHANGE_FEATURES
        sf = np.maximum(sf_i, seasonal.support_factor(st["coverage"], str(st["dates"][k])))
        z = np.abs((_anomaly(st, k)[:n] - anom_i[:n]) / (math.sqrt(2) * st["sigma"][:n] * sf)).max(0)
        v = st["valid"][k] & pix
        return float((z[v] > cfg.CHANGE_Z_THRESHOLD).mean()) if v.any() else 0.0
    s_j = support(j)
    thresh = max(0.25, 0.5 * s_j)
    probes = []
    lo, hi = 0, len(usable) - 1         
    probes.append({"step": 0, "scene": str(st["sids"][usable[hi]]), "date": str(st["dates"][usable[hi]]),
                   "support": round(s_j, 3), "supports_change": True, "role": "known change (detection)"})
    while lo < hi:
        mid = (lo + hi) // 2
        k = usable[mid]
        s = support(k)
        ok = s >= thresh
        probes.append({"step": len(probes), "scene": str(st["sids"][k]), "date": str(st["dates"][k]),
                       "support": round(s, 3), "supports_change": ok})
        if ok:
            hi = mid
        else:
            lo = mid + 1
    earliest = usable[hi]
    prev = usable[hi - 1] if hi > 0 else i
    return {
        "earliest_scene": str(st["sids"][earliest]), "earliest_date": str(st["dates"][earliest]),
        "last_without_change_scene": str(st["sids"][prev]), "last_without_change_date": str(st["dates"][prev]),
        "window": [str(st["dates"][i]), str(st["dates"][j])], "support_threshold": round(thresh, 3),
        "usable_scenes": len(usable), "skipped_masked": [str(st["sids"][k]) for k in skipped],
        "probes": probes, "comparisons_binary": len(probes),
        "comparisons_linear": usable.index(earliest) + 1 + (1 if earliest != j else 0),
    }
