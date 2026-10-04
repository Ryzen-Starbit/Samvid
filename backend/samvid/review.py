from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
import numpy as np
from . import db, ledger
from .archive import get_index
from .change import LABELS

CONFIRM_UP, REJECT_DOWN = 1.12, 0.85

def _w(key, default=1.0):
    r = db.one("SELECT weight FROM reranker WHERE key=?", (key,))
    return r["weight"] if r else default

def _set_w(key, val):
    db.run("INSERT OR REPLACE INTO reranker VALUES (?,?)", (key, float(np.clip(val, 0.4, 1.6))))

def _cand_vector(c):
    tiles = json.loads(c["tile_ids"])
    if not tiles:
        return None
    o = db.one("SELECT id FROM tile_obs WHERE tile_id=? AND scene_id=?", (tiles[0], c["after_scene"]))
    return get_index().vector(o["id"]) if o else None

def snapshot_hash(c: dict) -> str:
    keep = {k: c[k] for k in ("id", "aoi", "change_type", "direction", "bbox", "earliest_scene", "earliest_date",
                              "confidence", "area_px", "before_scene", "after_scene", "mask")}
    return hashlib.sha256(ledger.canonical(keep).encode()).hexdigest()

def rerank():
    cands = db.q("SELECT * FROM candidates")
    decided = [c for c in cands if c["status"] in ("confirmed", "rejected")]
    dvecs = [(c["status"], _cand_vector(c)) for c in decided]
    dvecs = [(s, v) for s, v in dvecs if v is not None]
    moves = []
    for c in cands:
        f = 1.0
        v = _cand_vector(c) if c["status"] == "pending" and dvecs else None
        if v is not None:
            for s, dv in dvecs:
                if float(v @ dv) >= 0.9:
                    f *= 1.08 if s == "confirmed" else 0.88
        score = c["confidence"] * _w(f"type:{c['change_type']}") * _w(f"aoi:{c['aoi']}") * f
        if abs(score - (c["rank_score"] or 0)) > 1e-6:
            moves.append((c["id"], round(c["rank_score"] or 0, 4), round(score, 4)))
        db.run("UPDATE candidates SET rank_score=? WHERE id=?", (round(score, 4), c["id"]))
    return moves

def queue(status="pending", aoi=None, change_type=None, limit=200):
    sql = "SELECT * FROM candidates WHERE 1=1"
    args = []
    if status and status != "all":
        sql += " AND status=?"
        args.append(status)
    if aoi:
        sql += " AND aoi=?"
        args.append(aoi)
    if change_type:
        sql += " AND change_type=?"
        args.append(change_type)
    sql += " ORDER BY rank_score DESC LIMIT ?"
    args.append(limit)
    out = []
    for c in db.q(sql, args):
        out.append(public(c))
    return out

def public(c: dict) -> dict:
    d = {k: v for k, v in c.items() if k != "mask"}
    for k in ("tile_ids", "bbox", "centroid", "search", "metrics", "processing"):
        if d.get(k):
            d[k] = json.loads(d[k])
    d["label"] = LABELS.get(c["change_type"], c["change_type"])
    return d

def decide(cid: str, decision: str, actor: str, note: str = "") -> dict:
    if decision not in ("confirmed", "rejected", "pending"):
        raise ValueError("decision must be confirmed, rejected or pending")
    c = db.one("SELECT * FROM candidates WHERE id=?", (cid,))
    if not c:
        raise KeyError(cid)
    snap = snapshot_hash(c)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.run("UPDATE candidates SET status=?, decided_by=?, decided_at=?, note=? WHERE id=?",
           (decision, actor, ts, note, cid))
    if decision != "pending":
        f = CONFIRM_UP if decision == "confirmed" else REJECT_DOWN
        _set_w(f"type:{c['change_type']}", _w(f"type:{c['change_type']}") * f)
        _set_w(f"aoi:{c['aoi']}", _w(f"aoi:{c['aoi']}") * (1 + (f - 1) / 2))
    moves = rerank()
    e = ledger.append(actor, "analyst.decision", cid, {
        "decision": decision, "note": note, "candidate_snapshot_sha256": snap,
        "change_type": c["change_type"], "confidence": c["confidence"], "earliest_date": c["earliest_date"],
        "previous_status": c["status"],
        "reranker": {"type_weight": round(_w(f"type:{c['change_type']}"), 4), "aoi_weight": round(_w(f"aoi:{c['aoi']}"), 4)},
        "rank_changes": len(moves)})
    top = sorted(moves, key=lambda m: -abs(m[2] - m[1]))[:5]
    return {"candidate": public(db.one("SELECT * FROM candidates WHERE id=?", (cid,))), "ledger": e,
            "rerank": {"changed": len(moves), "top_moves": top}}
