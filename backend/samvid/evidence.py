from __future__ import annotations
import io
import json
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from . import config as cfg
from . import db, imagery as im, seasonal
from .archive import load_stack, tile_slices
from .monitor import unpack_mask

CACHE = cfg.DERIVED_DIR / "_cache"
CACHE.mkdir(parents=True, exist_ok=True)

def _png(arr) -> bytes:
    b = io.BytesIO()
    Image.fromarray(arr).save(b, "PNG", optimize=True)
    return b.getvalue()

def _scene_rgb(aoi, sid):
    p = cfg.DERIVED_DIR / aoi / f"{sid}.png"
    return np.array(Image.open(p).convert("RGB"))

def tile_thumb(obs_id: int, scale=4) -> bytes:
    cp = CACHE / f"thumb_{obs_id}_{scale}.png"
    if cp.exists():
        return cp.read_bytes()
    o = db.one("SELECT tile_id, scene_id, aoi FROM tile_obs WHERE id=?", (obs_id,))
    _, r, c = o["tile_id"].split(":")
    ys, xs = tile_slices()[(int(r), int(c))]
    rgb = _scene_rgb(o["aoi"], o["scene_id"])[ys, xs]
    img = Image.fromarray(rgb).resize((rgb.shape[1] * scale, rgb.shape[0] * scale), Image.NEAREST)
    b = io.BytesIO()
    img.save(b, "PNG")
    cp.write_bytes(b.getvalue())
    return b.getvalue()

def _crop_box(bbox, pad=14):
    x0, y0, x1, y1 = bbox
    w, h = x1 - x0, y1 - y0
    side = max(w, h) + 2 * pad
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    X0 = int(np.clip(cx - side // 2, 0, cfg.SCENE_PX - side)) if side < cfg.SCENE_PX else 0
    Y0 = int(np.clip(cy - side // 2, 0, cfg.SCENE_PX - side)) if side < cfg.SCENE_PX else 0
    side = min(side, cfg.SCENE_PX)
    return X0, Y0, X0 + side, Y0 + side

def candidate_image(cid: str, kind: str) -> bytes:
    cp = CACHE / f"cand_{cid}_{kind}.png"
    if cp.exists():
        return cp.read_bytes()
    c = db.one("SELECT * FROM candidates WHERE id=?", (cid,))
    bbox = json.loads(c["bbox"])
    X0, Y0, X1, Y1 = _crop_box(bbox)
    scene = {"before": c["before_scene"], "earliest": c["earliest_scene"], "after": c["after_scene"],
             "change": c["after_scene"], "before_cls": c["before_scene"], "after_cls": c["after_scene"],
             "quality_after": c["after_scene"]}[kind]
    if kind.endswith("_cls"):
        arr = np.array(Image.open(cfg.DERIVED_DIR / c["aoi"] / f"{scene}_cls.png").convert("RGB"))
    else:
        arr = _scene_rgb(c["aoi"], scene)
    crop = arr[Y0:Y1, X0:X1].copy()
    if kind == "change":
        m = unpack_mask(json.loads(c["mask"]))[Y0:Y1, X0:X1]
        red = np.array([235, 64, 52], np.float32)
        crop = crop.astype(np.float32)
        crop[m] = crop[m] * 0.35 + red * 0.65
        crop = crop.astype(np.uint8)
    if kind == "quality_after":
        qa = np.array(Image.open(cfg.DERIVED_DIR / c["aoi"] / f"{scene}_qa.png").convert("RGBA"))[Y0:Y1, X0:X1]
        a = qa[..., 3:4].astype(np.float32) / 255
        crop = (crop * (1 - a) + qa[..., :3] * a).astype(np.uint8)
    scale = max(2, int(320 / crop.shape[0]))
    img = Image.fromarray(crop).resize((crop.shape[1] * scale, crop.shape[0] * scale), Image.NEAREST)
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = bbox
    d.rectangle([(x0 - X0) * scale, (y0 - Y0) * scale, (x1 - X0) * scale - 1, (y1 - Y0) * scale - 1],
                outline=(255, 214, 10), width=2)
    b = io.BytesIO()
    img.save(b, "PNG")
    cp.write_bytes(b.getvalue())
    return b.getvalue()

def scene_image(sid: str, layer: str = "rgb") -> bytes:
    s = db.one("SELECT aoi FROM scenes WHERE id=?", (sid,))
    suffix = {"rgb": "", "qa": "_qa", "cls": "_cls"}[layer]
    return (cfg.DERIVED_DIR / s["aoi"] / f"{sid}{suffix}.png").read_bytes()

def tile_series(tile_id: str) -> dict:
    aoi = tile_id.split(":")[0]
    rows = db.q("""SELECT o.id, o.acq_date, o.ndvi, o.ndvi_expected, o.ndvi_std, o.usable_frac, o.f_built, o.f_water,
                          o.f_veg, o.f_bare, o.f_disturbed, o.vehicle_count, s.status, s.cloud_frac, s.haze
                   FROM tile_obs o JOIN scenes s ON s.id=o.scene_id
                   WHERE o.tile_id=? AND o.sensor='S2' ORDER BY o.acq_date""", (tile_id,))
    st = load_stack(aoi)
    _, r, c = tile_id.split(":")
    ys, xs = tile_slices()[(int(r), int(c))]
    monthly = []
    for m in range(1, 13):
        e = seasonal.expected(st["coef"], f"2024-{m:02d}-15")[4][ys, xs].mean()
        monthly.append({"month": m, "expected": round(float(e), 3), "sigma": round(float(st["sigma"][4][ys, xs].mean()), 3)})
    for r_ in rows:
        if r_["ndvi"] is not None and r_["ndvi_expected"] is not None:
            r_["z"] = round((r_["ndvi"] - r_["ndvi_expected"]) / max(r_["ndvi_std"], 1e-3), 2)
    return {"tile_id": tile_id, "observations": rows, "monthly_baseline": monthly,
            "training_window": [str(st["dates"][0]), "first 12 months"]}
