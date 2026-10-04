from __future__ import annotations
import json
import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from samvid import config as cfg, db, synth  
from samvid.archive import month_idx  
from samvid.change import FAMILY  
from samvid.monitor import unpack_mask  

GT_FAMILY = {"construction": "construction", "clearance": "clearance", "water_expansion": "water",
             "road_development": "road", "vehicle_concentration": "vehicle"}

def gt_masks():
    out = []
    for aoi in synth.define_aois():
        rng = np.random.default_rng(aoi.seed)
        base, _ = synth.build_base(aoi, rng)
        for ev in aoi.events:
            k_end = ev.end_idx if ev.type != "vehicle_concentration" else ev.start_idx
            before = synth.render_class_map(aoi, base, max(0, ev.start_idx - 1))
            after = synth.render_class_map(aoi, base, k_end)
            m = before != after
            obs = ev.start_idx
            for k in range(ev.start_idx, k_end + 1):
                ck = synth.render_class_map(aoi, base, k)
                sep = (ck != before) & ~((ck == synth.DISTURBED) & (before == synth.BARE))
                if (sep & m).sum() >= 0.25 * max(1, m.sum()):
                    obs = k
                    break
            out.append({"event": ev, "mask": m, "aoi": aoi.id, "observable": obs})
    return out

def main():
    gts = gt_masks()
    cands = db.q("SELECT * FROM candidates")
    for c in cands:
        c["_mask"] = unpack_mask(json.loads(c["mask"]))
        c["_family"] = FAMILY[c["change_type"]]
        c["_matched"] = False
    per_event = []
    for g in gts:
        ev = g["event"]
        fam = GT_FAMILY[ev.type]
        hits = []
        for c in cands:
            if c["aoi"] != g["aoi"]:
                continue
            inter = (c["_mask"] & g["mask"]).sum()
            if inter and (inter >= 0.2 * c["_mask"].sum() or inter >= 0.2 * g["mask"].sum()):
                c["_matched"] = True
                hits.append(c)
        typed = [c for c in hits if c["_family"] == fam]
        best = min(typed or hits, key=lambda c: (c["earliest_date"], -c["confidence"])) if hits else None
        err = err_obs = None
        if best:
            err = month_idx(best["earliest_date"]) - ev.start_idx
            err_obs = month_idx(best["earliest_date"]) - g["observable"]
        per_event.append({"event": ev.id, "type": ev.type, "aoi": ev.aoi, "growth": ev.growth, "note": ev.note,
                          "detected": bool(hits), "type_correct": bool(typed),
                          "candidate": best["id"] if best else None,
                          "predicted_type": best["change_type"] if best else None,
                          "confidence": best["confidence"] if best else None,
                          "true_start": synth.MONTHS[ev.start_idx], "earliest_found": best["earliest_date"] if best else None,
                          "earliest_error_months": err,
                          "observable_onset": synth.MONTHS[g["observable"]], "error_vs_observable": err_obs})
    tp = sum(c["_matched"] for c in cands)
    searches = [json.loads(c["search"]) for c in cands]
    pair_stats = []
    for a in db.q("SELECT id FROM aois"):
        pair_stats += db.kv_get(f"pair_stats:{a['id']}", [])
    errs = [abs(e["error_vs_observable"]) for e in per_event if e["error_vs_observable"] is not None]
    errs_true = [abs(e["earliest_error_months"]) for e in per_event if e["earliest_error_months"] is not None]
    summary = {
        "events": len(per_event),
        "events_detected": sum(e["detected"] for e in per_event),
        "events_type_correct": sum(e["type_correct"] for e in per_event),
        "recall": round(sum(e["detected"] for e in per_event) / len(per_event), 3),
        "queue_candidates": len(cands),
        "queue_true_positives": int(tp),
        "queue_precision": round(tp / max(1, len(cands)), 3),
        "earliest_abs_error_vs_true_onset_months_mean": round(float(np.mean(errs_true)), 2) if errs_true else None,
        "earliest_within_1_month_of_true_onset": sum(1 for e in errs_true if e <= 1),
        "binary_search_comparisons": int(sum(s["comparisons_binary"] for s in searches)),
        "linear_scan_comparisons": int(sum(s["comparisons_linear"] for s in searches)),
        "naive_change_objects": int(sum(p["naive_objects"] for p in pair_stats)),
        "seasonal_change_objects": int(sum(p["seasonal_objects"] for p in pair_stats)),
        "naive_changed_px": int(sum(p["naive_changed_px"] for p in pair_stats)),
        "seasonal_changed_px": int(sum(p["seasonal_changed_px"] for p in pair_stats)),
        "suppressed_false_alarms": int(sum(p["suppressed_false_alarms"] for p in pair_stats)),
    }
    out = {"summary": summary, "per_event": per_event,
           "false_positives": [{"id": c["id"], "aoi": c["aoi"], "type": c["change_type"], "confidence": c["confidence"],
                                "bbox": json.loads(c["bbox"])} for c in cands if not c["_matched"]]}
    (cfg.DATA_DIR / "evaluation.json").write_text(json.dumps(out, indent=1, default=str))
    return out

if __name__ == "__main__":
    o = main()
    print(json.dumps(o["summary"], indent=1))
    for e in o["per_event"]:
        print(f"  {e['event']:<10} {e['type']:<22} det={e['detected']!s:<5} type={e['type_correct']!s:<5} "
              f"pred={e['predicted_type']!s:<22} start={e['true_start']} found={e['earliest_found']} err={e['earliest_error_months']}")
    print("false positives:")
    for f in o["false_positives"]:
        print("  ", f)
