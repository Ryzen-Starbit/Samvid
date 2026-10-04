from __future__ import annotations
import shutil
import sys
import time
import warnings
from pathlib import Path
warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from samvid import config as cfg  

def main():
    regen = "--regen" in sys.argv
    t0 = time.time()
    if regen or not any(cfg.ARCHIVE_DIR.glob("*/*.tif")):
        from samvid import synth
        print("[1/7] generating synthetic archive ...")
        synth.generate()
    else:
        print("[1/7] using existing archive")
    # reset derived state
    for p in (cfg.DB_PATH, cfg.LEDGER_PATH, Path(str(cfg.DB_PATH) + "-wal"), Path(str(cfg.DB_PATH) + "-shm")):
        p.unlink(missing_ok=True)
    for d in (cfg.DERIVED_DIR, cfg.INDEX_DIR):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
    from samvid import archive, auth, db, discovery, ledger, monitor  
    for f in archive.INCOMING_DIR.glob("*/*.tif"):
        shutil.move(str(f), cfg.ARCHIVE_DIR / f.parent.name / f.name)
    newest = sorted((cfg.ARCHIVE_DIR / "AOI-PLN").glob("S2_*.tif"))[-1]
    (archive.INCOMING_DIR / "AOI-PLN").mkdir(parents=True, exist_ok=True)
    shutil.move(str(newest), archive.INCOMING_DIR / "AOI-PLN" / newest.name)
    print(f"      held back {newest.name} in data/incoming/ for the live ingestion demo")

    ledger.append("system:setup", "system.setup", "archive", {"archive": str(cfg.ARCHIVE_DIR), "tile_px": cfg.TILE_PX,
                                                              "min_usable_fraction": cfg.MIN_USABLE_FRACTION,
                                                              "change_z": cfg.CHANGE_Z_THRESHOLD})
    archive._register_aois()
    print("[2/7] ingest + quality control + seasonal baselines ...")
    raw = {}
    for a in [r["id"] for r in db.q("SELECT id FROM aois ORDER BY id")]:
        raw[a] = archive.process_aoi(a)
    print("[3/7] embeddings + FAISS index ...")
    archive.build_embeddings(raw)
    print("[4/7] monitoring run (change analysis -> review queue) ...")
    for a in raw:
        monitor.run_aoi(a)
    print("[5/7] discovery + change-acceleration hotspots ...")
    discovery.run(verbose=False)
    print("[6/7] users")
    auth.seed()
    print("[7/7] evaluation against ground truth ...")
    from scripts import evaluate
    s = evaluate.main()["summary"]
    print("      recall %.2f | queue precision %.2f | earliest-obs mean error %.2f months | binary %d vs linear %d comparisons"
          % (s["recall"], s["queue_precision"], s["earliest_abs_error_vs_true_onset_months_mean"],
             s["binary_search_comparisons"], s["linear_scan_comparisons"]))
    print(f"done in {time.time() - t0:.0f}s - ledger verify: {ledger.verify()['valid']}")

if __name__ == "__main__":
    main()
