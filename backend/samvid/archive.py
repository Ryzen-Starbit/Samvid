from __future__ import annotations
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
from scipy import ndimage as ndi
from . import config as cfg
from . import db, embed, imagery as im, ledger, quality, seasonal
from .vindex import VectorIndex

T = cfg.TILE_PX
INCOMING_DIR = cfg.DATA_DIR / "incoming"
INCOMING_DIR.mkdir(parents=True, exist_ok=True)
_STACKS: dict[str, dict] = {}
_EMB = embed.get_embedder()
_INDEX: VectorIndex | None = None

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def month_idx(d: str) -> int:
    y, m = int(d[:4]), int(d[5:7])
    return (y - 2023) * 12 + (m - 7)   

def scene_id(kind, aoi, d):
    return f"{kind}_{aoi}_{d.replace('-', '')}"

def tile_slices(h=cfg.SCENE_PX, w=cfg.SCENE_PX):
    out = {}
    for r in range(h // T):
        for c in range(w // T):
            out[(r, c)] = (slice(r * T, (r + 1) * T), slice(c * T, (c + 1) * T))
    return out

def tile_id(aoi, r, c):
    return f"{aoi}:{r}:{c}"

def get_index() -> VectorIndex:
    global _INDEX
    if _INDEX is None:
        _INDEX = VectorIndex.load(embed.DIM)
    return _INDEX

def stack_path(aoi):
    return cfg.DERIVED_DIR / aoi / "stack.npz"

def load_stack(aoi) -> dict:
    if aoi not in _STACKS:
        z = np.load(stack_path(aoi), allow_pickle=True)
        _STACKS[aoi] = {k: z[k] for k in z.files}
        sp = cfg.DERIVED_DIR / aoi / "seasonal.npz"
        if sp.exists():
            s = np.load(sp)
            _STACKS[aoi]["coef"], _STACKS[aoi]["sigma"] = s["coef"], s["sigma"]
            _STACKS[aoi]["coverage"] = s["coverage"]
    return _STACKS[aoi]

def _register_aois():
    truth = json.loads(cfg.GROUND_TRUTH_PATH.read_text()) if cfg.GROUND_TRUTH_PATH.exists() else {"aois": []}
    names = {a["id"]: a for a in truth["aois"]}
    for d in sorted(p for p in cfg.ARCHIVE_DIR.iterdir() if p.is_dir()):
        a = names.get(d.name, {"id": d.name, "name": d.name, "lat": None, "lon": None, "bounds": None})
        db.run("INSERT OR REPLACE INTO aois VALUES (?,?,?,?,?)",
               (a["id"], a["name"], a["lat"], a["lon"], json.dumps(a["bounds"])))

def _scene_quality_png(cloud, shadow, snow, path):
    rgba = np.zeros(cloud.shape + (4,), np.uint8)
    rgba[cloud] = [255, 255, 255, 170]
    rgba[shadow] = [120, 60, 200, 170]
    rgba[snow] = [80, 220, 255, 170]
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(path)

def _process_optical(raw, ref, ref_water, pif):
    cloud, snow = quality.cloud_snow_masks(raw)
    shadow = quality.shadow_mask(raw, ref, cloud, ref_water)
    usable = ~(cloud | shadow | snow)
    corrected, reg, (ix, iy) = quality.coregister(raw, ref, usable)
    if ix or iy:
        ix, iy = -ix, -iy
        roll = lambda a: np.roll(np.roll(a, iy, 0), ix, 1)
        cloud, shadow, snow, usable = roll(cloud), roll(shadow), roll(snow), roll(usable)
        edge = np.zeros_like(usable)
        if iy > 0: edge[:iy] = True
        if iy < 0: edge[iy:] = True
        if ix > 0: edge[:, :ix] = True
        if ix < 0: edge[:, ix:] = True
        usable &= ~edge
    norm, rad = quality.radiometric_normalise(corrected, ref, usable, pif)
    return norm, usable, cloud, shadow, snow, reg, rad

def _scene_status(usable_frac, reg):
    if abs(reg["dx"]) > 6 or abs(reg["dy"]) > 6:
        return "held", "co-registration offset > 6 px"
    if reg["residual_px"] > cfg.MAX_REGISTRATION_ERROR_PX:
        return "held", f"residual registration error {reg['residual_px']} px"
    if usable_frac < 0.15:
        return "masked", f"only {usable_frac:.0%} clear pixels"
    return "ok", ""

def _tile_rows(aoi, sid, kind, d, norm, usable, cls, river_dist_m, sar=None):
    rows, raws = [], []
    mi = month_idx(d)
    for (r, c), (ys, xs) in tile_slices().items():
        v = usable[ys, xs]
        uf = float(v.mean())
        cl = cls[ys, xs]
        n = max(1, v.sum())
        frac = [float(((cl == k) & v).sum() / n) for k in range(7)]
        veh = (cl == im.VEHICLE) & v
        vcount = int(ndi.label(veh)[1])
        if kind == "S2":
            ix = im.indices(norm[:, ys, xs])
            ndvi = float(ix["ndvi"][v].mean()) if v.any() else None
            ndwi = float(ix["ndwi"][v].mean()) if v.any() else None
            ndbi = float(ix["ndbi"][v].mean()) if v.any() else None
            raw = embed.spectral_texture(norm[:, ys, xs], cl, v)
            road = (cl == im.ROAD) & v
            lin = 0.0
            if road.sum() > 6:
                yy, xx = np.nonzero(road)
                ev = np.linalg.eigvalsh(np.cov(np.stack([xx, yy])) + 1e-6 * np.eye(2))
                lin = float(1 - ev[0] / ev[1])
        else:
            ndvi = ndwi = ndbi = None
            lin = 0.0
            dummy = np.zeros((5, T, T), np.float32)
            raw = embed.spectral_texture(dummy, cl, v, sar=sar[ys, xs])
            raw[7:33] = 0.0
        rows.append([tile_id(aoi, r, c), sid, aoi, kind, d, mi, uf] + frac + [vcount, ndvi, None, None, ndwi, ndbi,
                    float(river_dist_m[ys, xs].min()), None, None, None, lin])
        raws.append(raw)
    return rows, raws

def _insert_obs(rows):
    db.many("""INSERT INTO tile_obs (tile_id, scene_id, aoi, sensor, acq_date, month_idx, usable_frac,
               f_water, f_veg, f_bare, f_built, f_road, f_vehicle, f_disturbed, vehicle_count, ndvi,
               ndvi_expected, ndvi_std, ndwi, ndbi, river_dist, new_built, d_water, d_veg, linearity)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)

def _insert_scene(sid, aoi, kind, meta, path, sha, cloud, shadow, snow, usable_frac, reg, rad, status, reason, ql):
    db.run("""INSERT OR REPLACE INTO scenes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        sid, aoi, kind, meta["acq_date"], month_idx(meta["acq_date"]), str(path), sha, meta["crs"], meta["bands"],
        meta["width"], meta["height"], json.dumps(meta["bounds"]), cloud, shadow, snow,
        int(bool(rad.get("haze_detected"))), usable_frac, reg.get("dx", 0), reg.get("dy", 0),
        reg.get("residual_px", 0), json.dumps(rad), status, reason, ql, now()))

def _sar_classes(db_img):
    cls = np.full(db_img.shape, im.BARE, np.uint8)
    sm = ndi.uniform_filter(db_img, 3)
    cls[sm < -17.5] = im.WATER
    cls[(sm > -11) & (sm <= -5)] = im.VEG
    cls[sm > -5] = im.BUILT
    return cls

def process_aoi(aoi: str, actor="system:ingest", skip: set | None = None, verbose=True):
    folder = cfg.ARCHIVE_DIR / aoi
    files = sorted(folder.glob("*.tif"))
    skip = skip or set()
    opt, sar = [], []
    for f in files:
        if f.name in skip:
            continue
        sc = im.read_scene(f)
        if sc["meta"]["problems"]:
            ledger.append(actor, "ingest.rejected", f.name, {"problems": sc["meta"]["problems"]})
            continue
        sc["path"], sc["sha"] = f, im.file_sha256(f)
        (sar if sc["meta"]["kind"] == "S1" else opt).append(sc)
    opt.sort(key=lambda s: s["meta"]["acq_date"])
    sar.sort(key=lambda s: s["meta"]["acq_date"])
    raws = np.stack([s["array"] for s in opt])
    H, W = raws.shape[-2:]
    # 1. clear-sky reference composite + pseudo-invariant features
    cs = [quality.cloud_snow_masks(r) for r in raws]
    clear = np.stack([~(c | s) for c, s in cs])
    masked = np.where(clear[:, None], raws, np.nan)
    ref = np.nanmedian(masked, axis=0)
    ref = np.where(np.isnan(ref), np.nanmedian(raws, axis=0), ref).astype(np.float32)
    ref_cls = im.classify(ref)
    ref_water = ref_cls == im.WATER
    ndvi_t = np.where(clear, (raws[:, 3] - raws[:, 2]) / (raws[:, 3] + raws[:, 2] + 1e-6), np.nan)
    pif = (np.nanstd(ndvi_t, 0) < 0.06) & np.isin(ref_cls, [im.BUILT, im.BARE, im.ROAD, im.WATER])
    river_dist = ndi.distance_transform_edt(~ref_water) * cfg.GSD_M if ref_water.any() else np.full((H, W), 1e5)
    norms, valids, clss, dates, sids = [], [], [], [], []
    for s in opt:
        meta = s["meta"]
        sid = scene_id("S2", aoi, meta["acq_date"])
        norm, usable, cloud, shadow, snow, reg, rad = _process_optical(s["array"], ref, ref_water, pif)
        uf = float(usable.mean())
        status, reason = _scene_status(uf, reg)
        if status == "held":
            usable[:] = False
        cls = im.classify(norm, usable)
        ql = cfg.DERIVED_DIR / aoi / f"{sid}.png"
        im.save_png(im.to_rgb8(norm), ql)
        _scene_quality_png(cloud, shadow, snow, cfg.DERIVED_DIR / aoi / f"{sid}_qa.png")
        im.save_png(im.class_rgb(cls), cfg.DERIVED_DIR / aoi / f"{sid}_cls.png")
        _insert_scene(sid, aoi, "S2", meta, s["path"], s["sha"], float(cloud.mean()), float(shadow.mean()),
                      float(snow.mean()), uf, reg, rad, status, reason, str(ql))
        ledger.append(actor, "ingest.scene", sid, {
            "file": s["path"].name, "sha256": s["sha"], "crs": meta["crs"], "bands": meta["bands"],
            "is_cog": meta["is_cog"], "cloud": round(float(cloud.mean()), 4), "shadow": round(float(shadow.mean()), 4),
            "snow": round(float(snow.mean()), 4), "haze": rad["haze_detected"], "registration": reg,
            "radiometric_fit": rad["gain_offset"], "usable_fraction": round(uf, 4), "status": status, "reason": reason})
        norms.append(norm.astype(np.float16)); valids.append(usable); clss.append(cls)
        dates.append(meta["acq_date"]); sids.append(sid)
    norm_stack = np.stack(norms).astype(np.float32)
    valid_stack = np.stack(valids)
    # 2. seasonal baseline model on the historical window (first 12 months)
    first = datetime.fromisoformat(dates[0])
    train = np.array([(datetime.fromisoformat(d) - first).days < 365 for d in dates])
    feats = seasonal.feature_stack(norm_stack)
    coef, sigma = seasonal.fit(feats, valid_stack, dates, train)
    (cfg.DERIVED_DIR / aoi).mkdir(parents=True, exist_ok=True)
    cov = seasonal.coverage(valid_stack, dates, train)
    np.savez_compressed(cfg.DERIVED_DIR / aoi / "seasonal.npz", coef=coef, sigma=sigma, coverage=cov)
    ledger.append(actor, "model.seasonal_baseline", aoi, {
        "model": "robust 2-harmonic per-pixel fit (Tukey IRLS)", "features": seasonal.FEATURES,
        "training_scenes": [s for s, t in zip(sids, train) if t],
        "sigma_median": [round(float(np.median(sigma[f])), 4) for f in range(sigma.shape[0])]})

    sar_dates, sar_ids, sar_imgs = [], [], []
    for s in sar:
        meta = s["meta"]
        sid = scene_id("S1", aoi, meta["acq_date"])
        dbi = s["array"][0]
        ql = cfg.DERIVED_DIR / aoi / f"{sid}.png"
        im.save_png(im.sar_to_rgb8(ndi.uniform_filter(dbi, 2)), ql)
        _insert_scene(sid, aoi, "S1", meta, s["path"], s["sha"], 0.0, 0.0, 0.0, 1.0,
                      {"dx": 0, "dy": 0, "residual_px": 0}, {"haze_detected": False}, "ok", "", str(ql))
        ledger.append(actor, "ingest.scene", sid, {"file": s["path"].name, "sha256": s["sha"], "crs": meta["crs"],
                                                   "bands": meta["bands"], "is_cog": meta["is_cog"], "status": "ok"})
        sar_dates.append(meta["acq_date"]); sar_ids.append(sid); sar_imgs.append(dbi.astype(np.float16))
    np.savez_compressed(stack_path(aoi), norm=norm_stack.astype(np.float16), valid=valid_stack,
                        cls=np.stack(clss), dates=np.array(dates), sids=np.array(sids), ref=ref,
                        ref_water=ref_water, pif=pif, river_dist=river_dist.astype(np.float32),
                        sar=np.stack(sar_imgs) if sar_imgs else np.zeros((0, H, W), np.float16),
                        sar_dates=np.array(sar_dates), sar_ids=np.array(sar_ids))
    _STACKS.pop(aoi, None)

    for (r, c), (ys, xs) in tile_slices(H, W).items():
        b = json.loads(db.one("SELECT bounds FROM aois WHERE id=?", (aoi,))["bounds"] or "null")
        tb = None
        if b:
            dx, dy = (b[2] - b[0]) / W, (b[3] - b[1]) / H
            tb = [b[0] + xs.start * dx, b[3] - ys.stop * dy, b[0] + xs.stop * dx, b[3] - ys.start * dy]
        db.run("INSERT OR REPLACE INTO tiles (id, aoi, row, col, bounds) VALUES (?,?,?,?,?)",
               (tile_id(aoi, r, c), aoi, r, c, json.dumps(tb)))
    all_raw = []
    for i, sid in enumerate(sids):
        rows, raws_ = _tile_rows(aoi, sid, "S2", dates[i], norm_stack[i], valid_stack[i], clss[i], river_dist)
        _insert_obs(rows)
        all_raw += raws_
    for i, sid in enumerate(sar_ids):
        dbi = sar_imgs[i].astype(np.float32)
        cls = _sar_classes(dbi)
        rows, raws_ = _tile_rows(aoi, sid, "S1", sar_dates[i], None, np.ones_like(cls, bool), cls, river_dist, sar=dbi)
        _insert_obs(rows)
        all_raw += raws_
    _seasonal_tile_fields(aoi)
    if verbose:
        print(f"  {aoi}: {len(sids)} optical, {len(sar_ids)} SAR, held={sum(1 for s in sids if db.one('SELECT status FROM scenes WHERE id=?', (s,))['status'] != 'ok')}")
    return np.stack(all_raw)

def _seasonal_tile_fields(aoi):
    st = load_stack(aoi)
    coef, sigma = st["coef"], st["sigma"]
    sl = tile_slices()
    obs = db.q("SELECT id, tile_id, acq_date, month_idx, f_built, f_water, f_veg, usable_frac, sensor, ndvi "
               "FROM tile_obs WHERE aoi=? AND sensor='S2'", (aoi,))
    cache = {}
    for o in obs:
        _, r, c = o["tile_id"].split(":")
        ys, xs = sl[(int(r), int(c))]
        if o["acq_date"] not in cache:
            cache[o["acq_date"]] = seasonal.expected(coef, o["acq_date"])[4]
        o["exp"] = float(cache[o["acq_date"]][ys, xs].mean())
        o["sig"] = float(sigma[4][ys, xs].mean())
        o["anom"] = (o["ndvi"] - o["exp"]) if o["ndvi"] is not None else 0.0
    by_tile = {}
    for o in obs:
        by_tile.setdefault(o["tile_id"], []).append(o)
    updates = []
    for o in obs:
        prev = [p for p in by_tile[o["tile_id"]]
                if 4 <= o["month_idx"] - p["month_idx"] <= 8 and p["usable_frac"] >= cfg.MIN_USABLE_FRACTION]
        if prev:
            p = max(prev, key=lambda p: p["usable_frac"])
            nb, dw, dv = o["f_built"] - p["f_built"], o["f_water"] - p["f_water"], o["anom"] - p["anom"]
        else:
            nb = dw = dv = 0.0
        updates.append((o["exp"], o["sig"], nb, dw, dv, o["id"]))
    db.many("UPDATE tile_obs SET ndvi_expected=?, ndvi_std=?, new_built=?, d_water=?, d_veg=? WHERE id=?", updates)

def build_embeddings(raw_by_aoi: dict, actor="system:ingest"):
    global _INDEX
    ids = [r["id"] for r in db.q("SELECT id FROM tile_obs ORDER BY aoi, id")]
    raw = np.concatenate([raw_by_aoi[a] for a in sorted(raw_by_aoi)])
    assert len(ids) == len(raw), (len(ids), len(raw))
    _EMB.fit_stats(raw)
    db.kv_set("embed_stats", {"mu": _EMB.mu.tolist(), "sd": _EMB.sd.tolist(), "name": _EMB.name})
    np.save(cfg.INDEX_DIR / "raw_features.npy", raw)
    np.save(cfg.INDEX_DIR / "raw_ids.npy", np.array(ids))
    vecs = _EMB.embed(raw)
    for f in (cfg.INDEX_DIR / "tiles.faiss", cfg.INDEX_DIR / "tiles.npz"):
        f.unlink(missing_ok=True)
    _INDEX = VectorIndex(embed.DIM)
    _INDEX.add(np.array(ids), vecs)
    _INDEX.save()
    db.run("UPDATE tile_obs SET embedded=1")
    ledger.append(actor, "index.build", "faiss", {"backend": _INDEX.backend, "embedder": _EMB.name,
                                                  "dim": embed.DIM, "vectors": _INDEX.ntotal})

def _embedder_with_stats():
    st = db.kv_get("embed_stats")
    _EMB.mu, _EMB.sd = np.array(st["mu"], np.float32), np.array(st["sd"], np.float32)
    return _EMB

def ingest_file(path: Path, actor: str) -> dict:
    sc = im.read_scene(path)
    meta = sc["meta"]
    if meta["problems"]:
        ledger.append(actor, "ingest.rejected", path.name, {"problems": meta["problems"]})
        return {"status": "rejected", "problems": meta["problems"]}
    sha = im.file_sha256(path)
    if db.one("SELECT id FROM scenes WHERE sha256=?", (sha,)):
        return {"status": "duplicate", "sha256": sha}
    aoi = path.parent.name if path.parent.parent == cfg.ARCHIVE_DIR else None
    if aoi is None:
        # locate AOI by footprint overlap
        for a in db.q("SELECT id, bounds FROM aois"):
            b = json.loads(a["bounds"] or "null")
            if b and abs((b[0] + b[2]) / 2 - (meta["bounds"][0] + meta["bounds"][2]) / 2) < 0.01:
                aoi = a["id"]
        if aoi is None:
            return {"status": "rejected", "problems": ["footprint does not match a registered AOI"]}
        dest = cfg.ARCHIVE_DIR / aoi / path.name
        shutil.copy(path, dest)
        path = dest
    st = load_stack(aoi)
    kind = meta["kind"]
    sid = scene_id(kind, aoi, meta["acq_date"])
    emb = _embedder_with_stats()
    index = get_index()
    if kind == "S2":
        norm, usable, cloud, shadow, snow, reg, rad = _process_optical(sc["array"], st["ref"], st["ref_water"], st["pif"])
        uf = float(usable.mean())
        status, reason = _scene_status(uf, reg)
        if status == "held":
            usable[:] = False
        cls = im.classify(norm, usable)
        ql = cfg.DERIVED_DIR / aoi / f"{sid}.png"
        im.save_png(im.to_rgb8(norm), ql)
        _scene_quality_png(cloud, shadow, snow, cfg.DERIVED_DIR / aoi / f"{sid}_qa.png")
        im.save_png(im.class_rgb(cls), cfg.DERIVED_DIR / aoi / f"{sid}_cls.png")
        _insert_scene(sid, aoi, "S2", meta, path, sha, float(cloud.mean()), float(shadow.mean()), float(snow.mean()),
                      uf, reg, rad, status, reason, str(ql))
        rows, raws = _tile_rows(aoi, sid, "S2", meta["acq_date"], norm, usable, cls, st["river_dist"])
        # append to stack (keeps seasonal model fixed - no refit needed)
        order = np.argsort(np.append(st["dates"], meta["acq_date"]))
        newst = dict(st)
        newst["norm"] = np.concatenate([st["norm"], norm[None].astype(np.float16)])[order]
        newst["valid"] = np.concatenate([st["valid"], usable[None]])[order]
        newst["cls"] = np.concatenate([st["cls"], cls[None]])[order]
        newst["dates"] = np.append(st["dates"], meta["acq_date"])[order]
        newst["sids"] = np.append(st["sids"], sid)[order]
        payload_q = {"cloud": round(float(cloud.mean()), 4), "registration": reg, "haze": rad["haze_detected"],
                     "usable_fraction": round(uf, 4), "status": status}
    else:
        dbi = sc["array"][0]
        ql = cfg.DERIVED_DIR / aoi / f"{sid}.png"
        im.save_png(im.sar_to_rgb8(ndi.uniform_filter(dbi, 2)), ql)
        _insert_scene(sid, aoi, "S1", meta, path, sha, 0, 0, 0, 1.0, {"dx": 0, "dy": 0, "residual_px": 0},
                      {"haze_detected": False}, "ok", "", str(ql))
        cls = _sar_classes(dbi)
        rows, raws = _tile_rows(aoi, sid, "S1", meta["acq_date"], None, np.ones_like(cls, bool), cls,
                                st["river_dist"], sar=dbi)
        newst = dict(st)
        newst["sar"] = np.concatenate([st["sar"], dbi[None].astype(np.float16)])
        newst["sar_dates"] = np.append(st["sar_dates"], meta["acq_date"])
        newst["sar_ids"] = np.append(st["sar_ids"], sid)
        payload_q = {"status": "ok"}
    before = index.ntotal
    _insert_obs(rows)
    new_ids = [r["id"] for r in db.q("SELECT id FROM tile_obs WHERE scene_id=? ORDER BY id", (sid,))]
    index.add(np.array(new_ids), emb.embed(np.stack(raws)))
    index.save()
    db.run("UPDATE tile_obs SET embedded=1 WHERE scene_id=?", (sid,))
    save = {k: v for k, v in newst.items() if k not in ("coef", "sigma", "coverage")}
    np.savez_compressed(stack_path(aoi), **save)
    _STACKS.pop(aoi, None)
    _seasonal_tile_fields(aoi)
    e1 = ledger.append(actor, "ingest.scene", sid, {"file": path.name, "sha256": sha, "crs": meta["crs"],
                                                   "bands": meta["bands"], "is_cog": meta["is_cog"], **payload_q})
    e2 = ledger.append(actor, "index.incremental_add", sid, {"vectors_added": len(new_ids), "index_before": before,
                                                            "index_after": index.ntotal, "rebuild": False})
    return {"status": "ingested", "scene_id": sid, "aoi": aoi, "sensor": kind, "quality": payload_q,
            "vectors_added": len(new_ids), "index_before": before, "index_after": index.ntotal,
            "ledger": [e1["seq"], e2["seq"]]}
