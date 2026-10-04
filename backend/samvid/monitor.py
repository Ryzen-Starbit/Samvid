from __future__ import annotations
import base64
import hashlib
import json
import math
from datetime import datetime, timezone
import numpy as np
from scipy import ndimage as ndi
from . import config as cfg
from . import db, ledger
from .archive import load_stack, month_idx, tile_id
from .change import FAMILY, LABELS, detect_pair, earliest_observation

T = cfg.TILE_PX
QUEUE_MIN_CONFIDENCE = 0.55
PHENOLOGY_SENSITIVE = {"clearance", "vegetation"}
PHENOLOGY_MIN_Z = 4.0
MONITOR_START = 12          
PERSISTENT = {"construction", "clearance", "water", "road"}
MODEL_VERSIONS = {
    "quality": "rule-masks v1.2 + phase-correlation coreg + PIF normalisation",
    "seasonal": "robust 2-harmonic baseline v1.0",
    "change": "anomaly z-score + HDBSCAN(min_cluster_size=12) v1.1",
    "earliest": "binary-search earliest-observation v1.0",
}

def _pack_mask(m: np.ndarray, bbox):
    x0, y0, x1, y1 = bbox
    crop = m[y0:y1, x0:x1]
    return {"bbox": bbox, "shape": list(crop.shape), "bits": base64.b64encode(np.packbits(crop).tobytes()).decode()}

def unpack_mask(d, shape=(cfg.SCENE_PX, cfg.SCENE_PX)):
    x0, y0, x1, y1 = d["bbox"]
    bits = np.unpackbits(np.frombuffer(base64.b64decode(d["bits"]), np.uint8))[: d["shape"][0] * d["shape"][1]]
    out = np.zeros(shape, bool)
    out[y0:y1, x0:x1] = bits.reshape(d["shape"]).astype(bool)
    return out

def anchors(aoi):
    st = load_stack(aoi)
    mi = np.array([month_idx(str(d)) for d in st["dates"]])
    vf = st["valid"].reshape(len(mi), -1).mean(1)
    out = []
    for q0 in range(MONITOR_START, mi.max() + 1, 3):
        cand = [k for k in range(len(mi)) if q0 - 1 <= mi[k] <= q0 + 1 and vf[k] >= 0.6]
        if cand:
            out.append(max(cand, key=lambda k: (vf[k], -abs(mi[k] - q0))))
    last = len(mi) - 1
    if vf[last] >= 0.6 and last not in out and mi[last] - mi[out[-1]] >= 1:
        out.append(last)
    return sorted(set(out))

def _period_label(d):
    y, m = int(d[:4]), int(d[5:7])
    return f"{y}-Q{(m - 1) // 3 + 1}"

def run_aoi(aoi: str, actor="system:monitor", verbose=True):
    st = load_stack(aoi)
    reg = {s["id"]: s["reg_error"] or 0 for s in db.q("SELECT id, reg_error FROM scenes WHERE aoi=?", (aoi,))}
    anc = anchors(aoi)
    db.run("DELETE FROM tile_change WHERE aoi=?", (aoi,))
    prev_status = {c["id"]: c for c in db.q("SELECT id, status, decided_by, decided_at, note FROM candidates WHERE aoi=?", (aoi,))}
    db.run("DELETE FROM candidates WHERE aoi=?", (aoi,))
    tracks = []
    anomaly_only = 0
    pair_stats = []
    tc_rows = []
    for p, (i, j) in enumerate(zip(anc[:-1], anc[1:])):
        res = detect_pair(aoi, i, j, True, reg)
        naive = detect_pair(aoi, i, j, False, reg)
        seasonal_mask = res["changed_mask"]
        objs_mask = np.zeros_like(seasonal_mask)
        for o in res["objects"]:
            objs_mask |= o["mask"]
        nlab = naive["label_map"]
        suppressed = 0
        for o in naive["objects"]:
            m = nlab == o["label"]
            if (m & ndi.binary_dilation(objs_mask, iterations=2)).sum() < 0.1 * m.sum():
                suppressed += 1
        pair_stats.append({"pair": res["pair"], "dates": res["dates"], "seasonal_objects": len(res["objects"]),
                           "seasonal_changed_px": res["changed_px"], "naive_objects": len(naive["objects"]),
                           "naive_changed_px": naive["changed_px"], "suppressed_false_alarms": suppressed})
        queued = []
        for o in res["objects"]:
            fam = FAMILY[o["type"]]
            weak_veg = fam in PHENOLOGY_SENSITIVE and o["z_median"] < PHENOLOGY_MIN_Z
            if o["type"] == "unclassified" or o["confidence"] < QUEUE_MIN_CONFIDENCE or weak_veg:
                anomaly_only += 1          # logged, not queued for analyst review
                continue
            o["pair_idx"], o["i"], o["j"] = p, i, j
            hit = None
            oy, ox = np.nonzero(o["mask"])
            oc = np.array([ox.mean(), oy.mean()])
            for t in tracks:
                last_m = t["dets"][-1]["mask"]
                ty, tx = np.nonzero(last_m)
                close = np.hypot(*(oc - np.array([tx.mean(), ty.mean()]))) < 40
                overlap = (ndi.binary_dilation(last_m, iterations=2) & o["mask"]).sum()
                if t["family"] == fam and close and overlap >= 0.1 * min(o["mask"].sum(), last_m.sum()):
                    hit = t
                    break
            if hit:
                hit["dets"].append(o)
                hit["mask"] = hit["mask"] | o["mask"]
            else:
                tracks.append({"family": fam, "dets": [o], "mask": o["mask"].copy()})
            queued.append(o)
        period = _period_label(res["dates"][1])
        for (r, c) in [(r, c) for r in range(cfg.SCENE_PX // T) for c in range(cfg.SCENE_PX // T)]:
            sl = (slice(r * T, (r + 1) * T), slice(c * T, (c + 1) * T))
            hits = [(int(o["mask"][sl].sum()), o["type"]) for o in queued if o["mask"][sl].any()]
            om = sum(h[0] for h in hits)
            nm = int(naive["changed_mask"][sl].sum())
            if om or nm:
                tc_rows.append((tile_id(aoi, r, c), aoi, p, period, om, nm, max(hits)[1] if hits else None))
    db.many("INSERT INTO tile_change VALUES (?,?,?,?,?,?,?)", tc_rows)
    n_written = 0
    for t in tracks:
        dets = t["dets"]
        first, last = dets[0], dets[-1]
        best = max(dets, key=lambda o: o["confidence"])
        if t["family"] == "vehicle":
            appear = [o for o in dets if o["type"] == "vehicle_concentration"]
            if appear:
                best = max(appear, key=lambda o: o["confidence"])
                last = best
        types = [o["type"] for o in dets]
        ctype = max(set(types), key=types.count)
        if t["family"] == "construction" and "construction" in types:
            ctype = "construction"
        if t["family"] == "vehicle":
            ctype = "vehicle_concentration"
        mask = t["mask"]
        ys, xs = np.nonzero(mask)
        bbox = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
        if t["family"] in PERSISTENT:
            m0 = first["mask"]
            base = next((k for k in range(first["i"] + 1)
                         if month_idx(str(st["dates"][k])) >= MONITOR_START - 1
                         and (st["valid"][k] & m0).sum() >= 0.6 * m0.sum()), first["i"])
            search = earliest_observation(aoi, m0, base, first["j"])
        else:
            search = earliest_observation(aoi, first["mask"], first["i"], first["j"])
        conf = best["confidence"]
        persistence = sum(1 for o in dets)
        if t["family"] in PERSISTENT and persistence > 1:
            conf = min(0.98, conf + 0.04 * (persistence - 1))
        direction = first["direction"] if first["direction"] in ("appearance", "disappearance") else last["direction"]
        if t["family"] == "vehicle":
            appeared = [o for o in dets if o["type"] == "vehicle_concentration"]
            gone = [o for o in dets if o["type"] == "vehicle_dispersal"]
            direction = "appearance → disappearance (transient)" if appeared and gone else "appearance"
        tiles = sorted({tile_id(aoi, int(y) // T, int(x) // T) for y, x in zip(ys[::7], xs[::7])})
        b = json.loads(db.one("SELECT bounds FROM aois WHERE id=?", (aoi,))["bounds"])
        cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        lon = b[0] + (b[2] - b[0]) * cx / cfg.SCENE_PX
        lat = b[3] - (b[3] - b[1]) * cy / cfg.SCENE_PX
        cid = "CHG-" + hashlib.sha1(f"{aoi}|{t['family']}|{bbox[0]//16}|{bbox[1]//16}".encode()).hexdigest()[:8].upper()
        timeline = [{"pair": o["pair_idx"], "before": str(st["sids"][o["i"]]), "after": str(st["sids"][o["j"]]),
                     "date_after": str(st["dates"][o["j"]]), "type": o["type"], "direction": o["direction"],
                     "area_px": o["area_px"], "confidence": o["confidence"]} for o in dets]
        metrics = {k: best[k] for k in ("z_median", "hdbscan_prob", "quality", "registration_error_px", "elongation",
                                        "sar", "seasonal", "class_before", "class_after")}
        metrics["detections"] = timeline
        metrics["persistence"] = persistence
        area_growth = [o["dominant_area_after_px"] for o in dets]
        metrics["dominant_area_series_px"] = area_growth
        processing = [
            {"step": "ingest + QC", "detail": f"scenes {first['i']}..{last['j']} cloud/shadow/snow masked, co-registered, PIF-normalised", "model": MODEL_VERSIONS["quality"]},
            {"step": "seasonal normalisation", "detail": "anomalies vs per-pixel harmonic baseline", "model": MODEL_VERSIONS["seasonal"]},
            {"step": "change detection", "detail": f"{len(dets)} detection(s) over quarterly pairs, z>{cfg.CHANGE_Z_THRESHOLD}", "model": MODEL_VERSIONS["change"]},
            {"step": "earliest observation", "detail": f"{search['comparisons_binary']} comparisons (linear scan: {search['comparisons_linear']})", "model": MODEL_VERSIONS["earliest"]},
            {"step": "SAR corroboration", "detail": json.dumps(best["sar"]) if best["sar"] else "no SAR pair within 40 days", "model": "log-ratio VV"},
        ]
        old = prev_status.get(cid, {})
        row = (cid, aoi, json.dumps(tiles), json.dumps(bbox), json.dumps([round(lat, 6), round(lon, 6)]), ctype, direction,
               str(st["sids"][first["i"]]), str(st["sids"][last["j"]]),
               search["last_without_change_scene"], str(st["sids"][last["j"]]), search["earliest_scene"],
               search["earliest_date"], search["last_without_change_scene"], json.dumps(search), conf, conf,
               int(mask.sum()), float(mask.sum() * cfg.GSD_M ** 2), json.dumps(metrics, default=str),
               json.dumps(processing), old.get("status", "pending"), old.get("decided_by"), old.get("decided_at"),
               old.get("note"), -1, datetime.now(timezone.utc).isoformat(timespec="seconds"),
               json.dumps(_pack_mask(mask, bbox)))
        db.run("INSERT OR REPLACE INTO candidates VALUES (" + ",".join("?" * 28) + ")", row)
        ledger.append(actor, "model.change_candidate", cid, {
            "aoi": aoi, "type": ctype, "label": LABELS[ctype], "direction": direction, "bbox_px": bbox,
            "earliest": {"scene": search["earliest_scene"], "date": search["earliest_date"],
                         "binary_comparisons": search["comparisons_binary"],
                         "linear_comparisons": search["comparisons_linear"]},
            "confidence": conf, "models": MODEL_VERSIONS, "mask_sha256": hashlib.sha256(mask.tobytes()).hexdigest()})
        n_written += 1
    db.kv_set(f"pair_stats:{aoi}", pair_stats)
    db.kv_set(f"anchors:{aoi}", [str(st["sids"][k]) for k in anc])
    ledger.append(actor, "model.monitoring_run", aoi, {"anchors": [str(st["sids"][k]) for k in anc],
                                                      "pairs": len(pair_stats), "candidates": n_written,
                                                      "naive_objects": sum(p["naive_objects"] for p in pair_stats),
                                                      "seasonal_objects": sum(p["seasonal_objects"] for p in pair_stats),
                                                      "suppressed": sum(p["suppressed_false_alarms"] for p in pair_stats),
                                                      "not_queued_low_confidence_or_unclassified": anomaly_only})
    db.kv_set(f"not_queued:{aoi}", anomaly_only)
    if verbose:
        print(f"  {aoi}: {len(anc)} anchors, {n_written} candidates, "
              f"naive objs {sum(p['naive_objects'] for p in pair_stats)} vs seasonal {sum(p['seasonal_objects'] for p in pair_stats)}")
    return n_written
