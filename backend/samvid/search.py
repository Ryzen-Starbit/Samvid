from __future__ import annotations
import hashlib
import json
import math
import numpy as np
from . import config as cfg
from . import agent, db, embed, imagery as im, ledger
from .archive import get_index, _embedder_with_stats

W_CONCEPT, W_VISUAL = 0.7, 0.3

def _concept_scores(r: dict) -> dict:
    rd = r["river_dist"] if r["river_dist"] is not None else 1e5
    return {
        "built": min(1.0, (r["f_built"] or 0) / 0.25),
        "new_built": min(1.0, max(0.0, r["new_built"] or 0) / 0.12 + (r["f_disturbed"] or 0) / 0.3),
        "water": min(1.0, (r["f_water"] or 0) / 0.25),
        "near_water": math.exp(-max(0.0, rd) / 180.0),
        "vehicles": min(1.0, (r["vehicle_count"] or 0) / 5.0),
        "open_ground": min(1.0, (r["f_bare"] or 0) / 0.55),
        "vegetation": min(1.0, (r["f_veg"] or 0) / 0.6),
        "road": min(1.0, (r["f_road"] or 0) / 0.06 * 0.6 + (r["linearity"] or 0) * 0.4),
        "clearance": min(1.0, max(0.0, -(r["d_veg"] or 0)) / 0.3)      # seasonal-anomaly NDVI drop ...
                     * (1 - min(1.0, max(0.0, r["d_water"] or 0) / 0.08)),     # ... not caused by flooding
        "water_change": min(1.0, abs(r["d_water"] or 0) / 0.08),
        "recent": min(1.0, max(max(0.0, r["new_built"] or 0) / 0.12, abs(r["d_water"] or 0) / 0.08,
                                max(0.0, -(r["d_veg"] or 0)) / 0.3)),
        "large": min(1.0, (r["vehicle_count"] or 0) / 12.0 + (r["f_built"] or 0) / 0.5),
    }

def _explain(r: dict, cs: dict, concepts: dict) -> list[str]:
    out = []
    for k in concepts:
        v = cs.get(k, 0)
        if k == "built":
            out.append(f"built-up {r['f_built']:.0%}")
        elif k == "new_built":
            out.append(f"built-up grew {max(0, r['new_built'] or 0):.0%} in ~6 months")
        elif k == "near_water":
            rd = r['river_dist'] or 0
            out.append("next to water" if rd < 30 else f"{rd:.0f} m from water" if rd < 5000 else "no water nearby")
        elif k == "vehicles":
            out.append(f"{r['vehicle_count']} vehicle-sized objects" if r['vehicle_count'] else "no vehicles visible")
        elif k == "water":
            out.append(f"water {r['f_water']:.0%}")
        elif k == "open_ground":
            out.append(f"open ground {r['f_bare']:.0%}")
        elif k == "road":
            out.append(f"roads cover {r['f_road']:.0%}" + (", straight new track" if (r['linearity'] or 0) > 0.9 else ""))
        elif k == "clearance":
            out.append(f"{'much less' if (r['d_veg'] or 0) < -0.2 else 'less'} green than the season predicts")
        elif k == "water_change":
            out.append(f"water area {r['d_water']:+.0%} in ~6 months")
        elif k == "vegetation":
            out.append(f"vegetation {r['f_veg']:.0%}")
        elif k == "recent":
            out.append("changed in the last ~6 months" if v > 0.3 else "little recent change")
        elif k == "large":
            continue
        else:
            out.append(f"{k} {v:.2f}")
    return out

def _filtered_rows(aoi=None, date_from=None, date_to=None, sensor=None, min_quality=cfg.MIN_USABLE_FRACTION):
    sql = "SELECT * FROM tile_obs WHERE usable_frac >= ?"
    args = [min_quality]
    if aoi:
        sql += f" AND aoi IN ({','.join('?' * len(aoi))})"
        args += list(aoi)
    if date_from:
        sql += " AND acq_date >= ?"
        args.append(date_from)
    if date_to:
        sql += " AND acq_date <= ?"
        args.append(date_to)
    if sensor:
        sql += " AND sensor = ?"
        args.append(sensor)
    return db.q(sql, args)

def _row_out(r, score, parts, why):
    t = db.one("SELECT bounds, cluster_id FROM tiles WHERE id=?", (r["tile_id"],))
    return {"obs_id": r["id"], "tile_id": r["tile_id"], "aoi": r["aoi"], "scene_id": r["scene_id"], "sensor": r["sensor"],
            "date": r["acq_date"], "score": round(float(score), 4), "parts": parts, "why": why,
            "usable_frac": round(r["usable_frac"], 3), "bounds": json.loads(t["bounds"]) if t else None,
            "cluster_id": t["cluster_id"] if t else -1,
            "fractions": {k[2:]: round(r[k] or 0, 3) for k in ("f_water", "f_veg", "f_bare", "f_built", "f_road", "f_disturbed")},
            "vehicle_count": r["vehicle_count"], "thumb": f"/api/tiles/{r['id']}/thumb.png"}

def text_search(query: str, filters: dict | None = None, actor="analyst", one_per_tile=True):
    filters = filters or {}
    p = agent.plan(query)
    aoi = filters.get("aoi") or p.get("aoi") or None
    d0 = filters.get("date_from") or p.get("date_from")
    d1 = filters.get("date_to") or p.get("date_to")
    sensor = filters.get("sensor") or p.get("sensor") or "S2"
    top_k = int(filters.get("top_k") or p.get("top_k") or 24)
    rows = _filtered_rows(aoi, d0, d1, sensor)
    trace = list(p["trace"])
    trace.append({"step": "Apply filters", "detail": f"AOI={aoi or 'all'} dates={d0 or '…'}→{d1 or '…'} sensor={sensor}; "
                                                     f"{len(rows)} tile observations pass quality ≥ {cfg.MIN_USABLE_FRACTION:.0%}"})
    concepts = p["concepts"] or {}
    if not rows:
        return {"query": query, "plan": p, "results": [], "trace": trace}
    scored = []
    for r in rows:
        cs = _concept_scores(r)
        if concepts:
            logs = sum(w * math.log(0.05 + 0.95 * cs.get(k, 0)) for k, w in concepts.items())
            s = math.exp(logs / sum(concepts.values()))
        else:
            s = 0.0
        scored.append((s, r, cs))
    scored.sort(key=lambda x: -x[0])
    trace.append({"step": "Concept scoring", "detail": f"{len(concepts)} concept(s): {', '.join(concepts) or '—'}"})
    idx = get_index()
    seeds = [x[1]["id"] for x in scored[:15] if x[0] > 0.3] or [x[1]["id"] for x in scored[:5]]
    qv = np.mean([idx.vector(i) for i in seeds], 0)
    qv /= np.linalg.norm(qv) + 1e-9
    allowed = np.array([r["id"] for r in rows], np.int64)
    ids, sims = idx.search(qv, k=min(400, len(allowed)), allowed=allowed)
    sim = {int(i): float(s) for i, s in zip(ids, sims)}
    trace.append({"step": "Vector retrieval", "detail": f"{idx.backend}: query vector from {len(seeds)} seed tiles, "
                                                        f"{len(ids)} neighbours"})
    fused = []
    for s, r, cs in scored:
        v = (sim.get(r["id"], -1.0) + 1) / 2
        f = W_CONCEPT * s + W_VISUAL * v if concepts else v
        fused.append((f, s, v, r, cs))
    fused.sort(key=lambda x: -x[0])
    out, seen = [], set()
    for f, s, v, r, cs in fused:
        if one_per_tile and r["tile_id"] in seen:
            continue
        seen.add(r["tile_id"])
        out.append(_row_out(r, f, {"concept": round(s, 3), "visual": round(v, 3)}, _explain(r, cs, concepts)))
        if len(out) >= top_k:
            break
    trace.append({"step": "Fuse & rank", "detail": f"score = {W_CONCEPT}·concept + {W_VISUAL}·visual; best observation per tile; top {len(out)}"})
    digest = hashlib.sha256(json.dumps([(o["obs_id"], o["score"]) for o in out]).encode()).hexdigest()
    e = ledger.append(actor, "model.retrieval", f"query:{query[:60]}", {
        "query": query, "planner": p["planner"], "plan": {k: p[k] for k in ("concepts", "aoi", "date_from", "date_to", "sensor")},
        "filters": {"aoi": aoi, "date_from": d0, "date_to": d1, "sensor": sensor}, "embedder": embed.SpectralTextureEmbedder.name,
        "index": idx.backend, "results": len(out), "results_sha256": digest})
    trace.append({"step": "Ledger", "detail": f"entry #{e['seq']} {e['entry_hash'][:16]}…"})
    best = max((o["parts"]["concept"] for o in out), default=0.0)
    return {"query": query, "plan": {k: v for k, v in p.items() if k != "trace"}, "results": out, "trace": trace,
            "ledger_seq": e["seq"], "best_concept_score": best,
            "strong_matches": sum(1 for o in out if o["parts"]["concept"] >= 0.5),
            "weak": bool(concepts) and best < 0.4}

def image_search(obs_id: int | None = None, chip_path=None, filters: dict | None = None, actor="analyst", k=24):
    filters = filters or {}
    idx = get_index()
    if obs_id is not None:
        q = idx.vector(int(obs_id))
        src = f"tile observation {obs_id}"
        if q is None:
            return {"error": "unknown observation"}
    else:
        sc = im.read_scene(chip_path)
        if sc["meta"]["kind"] != "S2" or sc["meta"]["count"] < 5:
            return {"error": "query chip must be a 5-band optical GeoTIFF (B2,B3,B4,B8,B11). RGB-only chips need the RemoteCLIP backend."}
        r = sc["array"]
        valid = np.ones(r.shape[1:], bool)
        raw = embed.spectral_texture(r, im.classify(r), valid)
        q = _embedder_with_stats().embed(raw[None])[0]
        src = f"uploaded chip {chip_path.name}"
    rows = _filtered_rows(filters.get("aoi"), filters.get("date_from"), filters.get("date_to"), filters.get("sensor") or "S2")
    allowed = np.array([r["id"] for r in rows], np.int64)
    ids, sims = idx.search(q, k=min(len(allowed), k * 6), allowed=allowed)
    byid = {r["id"]: r for r in rows}
    src_tile = db.one("SELECT tile_id FROM tile_obs WHERE id=?", (obs_id,))["tile_id"] if obs_id is not None else None
    out, seen = [], {src_tile}
    for i, s in zip(ids, sims):
        r = byid[int(i)]
        if r["tile_id"] in seen:
            continue
        seen.add(r["tile_id"])
        out.append(_row_out(r, float(s), {"visual": round(float(s), 3)}, [f"looks {max(0.0, float(s)):.0%} alike"]))
        if len(out) >= k:
            break
    e = ledger.append(actor, "model.retrieval_image", src, {"source": src, "index": idx.backend, "results": len(out),
                                                            "results_sha256": hashlib.sha256(json.dumps([o["obs_id"] for o in out]).encode()).hexdigest()})
    return {"query": src, "results": out, "trace": [
        {"step": "Embed query", "detail": embed.SpectralTextureEmbedder.name},
        {"step": "Vector retrieval", "detail": f"{idx.backend}, {len(allowed)} candidates after filters"},
        {"step": "Ledger", "detail": f"entry #{e['seq']}"}], "ledger_seq": e["seq"]}