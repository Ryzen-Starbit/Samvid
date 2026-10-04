from __future__ import annotations
import json
import numpy as np
from sklearn.cluster import HDBSCAN
from . import config as cfg
from . import db, ledger
from .archive import get_index

CLASS_WORDS = {"f_water": "water", "f_veg": "vegetation", "f_bare": "open ground", "f_built": "built-up",
               "f_road": "roads", "f_vehicle": "vehicles", "f_disturbed": "earthworks"}

def latest_obs_per_tile(sensor="S2"):
    rows = db.q(f"""SELECT o.* FROM tile_obs o JOIN (
                      SELECT tile_id, MAX(acq_date) d FROM tile_obs
                      WHERE sensor=? AND usable_frac >= ? GROUP BY tile_id) l
                    ON o.tile_id = l.tile_id AND o.acq_date = l.d WHERE o.sensor=?""",
                (sensor, cfg.MIN_USABLE_FRACTION, sensor))
    return rows

def _label(profile):
    top = sorted(((v, k) for k, v in profile.items() if k in CLASS_WORDS), reverse=True)
    words = [CLASS_WORDS[k] for v, k in top[:2] if v > 0.08]
    if profile.get("near_water", 0) > 0.5 and "water" not in words:
        words.append("near water")
    return " + ".join(words) if words else "mixed"

def run(actor="system:discovery", verbose=True):
    idx = get_index()
    rows = latest_obs_per_tile()
    vecs = np.stack([idx.vector(r["id"]) for r in rows])
    hd = HDBSCAN(min_cluster_size=5, min_samples=2, copy=True)
    lab = hd.fit_predict(vecs)
    core = lab.copy()
    if (lab >= 0).any():
        cents = np.stack([vecs[lab == k].mean(0) for k in range(lab.max() + 1)])
        cents /= np.linalg.norm(cents, axis=1, keepdims=True)
        for i in np.where(lab < 0)[0]:
            sims = cents @ vecs[i]
            if sims.max() >= 0.6:
                lab[i] = int(sims.argmax())
    db.run("DELETE FROM clusters")
    db.run("UPDATE tiles SET cluster_id=-1")
    periods = sorted({r["period"] for r in db.q("SELECT DISTINCT period FROM tile_change")})
    tc = db.q("""SELECT tile_id, period, SUM(changed_px) px FROM tile_change
                 WHERE change_type IS NOT NULL AND change_type NOT LIKE 'vehicle%' GROUP BY tile_id, period""")
    by_tile = {}
    for r in tc:
        by_tile.setdefault(r["tile_id"], {})[r["period"]] = r["px"]
    types = {}
    for r in db.q("SELECT tile_id, change_type FROM tile_change WHERE change_type IS NOT NULL"):
        types.setdefault(r["tile_id"], []).append(r["change_type"])
    out = []
    for k in sorted(set(lab)):
        members = [rows[i] for i in range(len(rows)) if lab[i] == k]
        if k < 0:
            label, cid = "unclustered (noise)", -1
        else:
            cid = int(k)
        profile = {c: float(np.mean([m[c] for m in members])) for c in CLASS_WORDS}
        profile["near_water"] = float(np.mean([1.0 if (m["river_dist"] or 1e5) < 250 else 0.0 for m in members]))
        if k >= 0:
            label = _label(profile)
        series = []
        for p in periods:
            px = sum(by_tile.get(m["tile_id"], {}).get(p, 0) for m in members)
            series.append({"period": p, "changed_km2": round(px * cfg.GSD_M ** 2 / 1e6, 4), "changed_px": int(px)})
        rate = np.array([s["changed_km2"] for s in series])
        accel, hotspot, ratio = 0.0, False, None
        if len(rate) >= 4:
            half = len(rate) // 2
            early, late = rate[:half].mean(), rate[half:].mean()
            x = np.arange(len(rate))
            accel = float(np.polyfit(x, rate, 1)[0])
            ratio = float(late / early) if early > 0 else (float("inf") if late > 0 else None)
            recent = rate[-2:].mean()
            active_q = int((rate > 0.0005).sum())          # sustained, not a one-off event
            hotspot = bool(k >= 0 and active_q >= 3 and rate[-1] >= 0.01 and recent >= 2.0 * max(early, 1e-4)
                           and late >= 2.0 * max(early, 1e-4) and accel > 0)
        dom = [t for m in members for t in types.get(m["tile_id"], [])]
        dominant = max(set(dom), key=dom.count) if dom else None
        aois = sorted({m["aoi"] for m in members})
        summary = {"aois": aois, "dominant_change": dominant, "rate_ratio_late_vs_early": None if ratio in (None, float("inf")) else round(ratio, 2),
                   "latest_quarter_km2": series[-1]["changed_km2"] if series else 0}
        if k >= 0:
            db.run("INSERT INTO clusters VALUES (?,?,?,?,?,?,?,?,?)", (
                cid, label, len(members), json.dumps([m["tile_id"] for m in members]), json.dumps(profile),
                json.dumps(series), accel, int(hotspot), json.dumps(summary)))
            db.many("UPDATE tiles SET cluster_id=? WHERE id=?", [(cid, m["tile_id"]) for m in members])
        out.append({"id": cid, "label": label, "size": len(members), "hotspot": hotspot, "accel": accel, "summary": summary})
    for c in db.q("SELECT id, tile_ids FROM candidates"):
        t = json.loads(c["tile_ids"])
        cl = db.one("SELECT cluster_id FROM tiles WHERE id=?", (t[0],)) if t else None
        db.run("UPDATE candidates SET cluster_id=? WHERE id=?", (cl["cluster_id"] if cl else -1, c["id"]))
    hot = [o for o in out if o["hotspot"]]
    ledger.append(actor, "model.discovery", "archive", {
        "algorithm": "HDBSCAN(min_cluster_size=5, min_samples=2) on latest-observation tile embeddings + soft assignment (cos>=0.6)",
        "tiles": len(rows), "clusters": int((lab >= 0).max() + 1 if (lab >= 0).any() else 0),
        "noise_tiles": int((lab < 0).sum()), "soft_assigned": int(((core < 0) & (lab >= 0)).sum()), "hotspots": [{"cluster": o["id"], "label": o["label"],
                                                           "accel_km2_per_q": round(o["accel"], 5)} for o in hot]})
    if verbose:
        for o in out:
            print(f"  cluster {o['id']:>2} {o['label']:<34} n={o['size']:<3} hotspot={o['hotspot']} accel={o['accel']:.4f} {o['summary']}")
    return out

def similar_sites(obs_id: int, k: int = 12):
    idx = get_index()
    latest = latest_obs_per_tile()
    q = idx.vector(obs_id)
    if q is None:
        return []
    allowed = np.array([r["id"] for r in latest], np.int64)
    ids, sims = idx.search(q, k + 1, allowed=allowed)
    byid = {r["id"]: r for r in latest}
    src = db.one("SELECT tile_id FROM tile_obs WHERE id=?", (obs_id,))
    res = []
    for i, s in zip(ids, sims):
        r = byid[int(i)]
        if src and r["tile_id"] == src["tile_id"]:
            continue
        res.append({**r, "similarity": round(float(s), 4)})
    return res[:k]
