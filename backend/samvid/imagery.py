from __future__ import annotations
import hashlib
import json
from pathlib import Path
import numpy as np
import rasterio
from PIL import Image
from scipy import ndimage as ndi

WATER, VEG, BARE, BUILT, ROAD, VEHICLE, DISTURBED, MASKED = range(8)
CLASS_NAMES = ["water", "vegetation", "bare", "built", "road", "vehicle", "disturbed", "masked"]
CLASS_COLORS = np.array([
    [36, 104, 196], [52, 150, 74], [196, 170, 120], [214, 82, 60],
    [70, 70, 78], [250, 220, 40], [232, 140, 60], [0, 0, 0]], np.uint8)

EPS = 1e-6
REQUIRED_OPTICAL_BANDS = 5   

def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def read_scene(path: Path) -> dict:
    with rasterio.open(path) as src:
        tags = src.tags()
        meta = {
            "driver": src.driver,
            "is_cog": src.driver == "GTiff" and bool(src.overviews(1)) and src.profile.get("tiled", False),
            "crs": src.crs.to_string() if src.crs else None,
            "width": src.width, "height": src.height, "count": src.count,
            "dtype": src.dtypes[0],
            "bounds": list(src.bounds),
            "transform": list(src.transform)[:6],
            "sensor": tags.get("SENSOR", "unknown"),
            "acq_date": tags.get("ACQ_DATE"),
            "bands": tags.get("BANDS", ""),
            "scale": float(tags.get("SCALE", "1")),
        }
        arr = src.read().astype(np.float32)
    problems = []
    if not meta["crs"]:
        problems.append("missing CRS")
    if not meta["acq_date"]:
        problems.append("missing ACQ_DATE tag")
    is_sar = "VV" in meta["bands"] or meta["count"] == 1
    meta["kind"] = "S1" if is_sar else "S2"
    if not is_sar:
        if meta["count"] < REQUIRED_OPTICAL_BANDS:
            problems.append(f"expected {REQUIRED_OPTICAL_BANDS} optical bands (B2,B3,B4,B8,B11), found {meta['count']}")
        arr = arr / (meta["scale"] or 1.0)
    meta["problems"] = problems
    return {"array": arr, "meta": meta}

def indices(r: np.ndarray) -> dict:
    b, g, red, nir, swir = r
    return {
        "ndvi": (nir - red) / (nir + red + EPS),
        "ndwi": (g - nir) / (g + nir + EPS),
        "ndbi": (swir - nir) / (swir + nir + EPS),
        "br": b / (red + EPS),                     
        "ndsi": (g - swir) / (g + swir + EPS),
        "bright": (b + g + red) / 3,
    }

def classify(r: np.ndarray, valid: np.ndarray | None = None) -> np.ndarray:
    ix = indices(r)
    ndvi, ndwi, br, bright, nir = ix["ndvi"], ix["ndwi"], ix["br"], ix["bright"], r[3]
    cls = np.full(ndvi.shape, BARE, np.uint8)
    cls[(ndvi > 0.30)] = VEG
    cls[(ndvi < 0.30) & (br > 0.62) & (bright > 0.15)] = DISTURBED
    cls[(ndvi < 0.30) & (br > 0.78) & (bright > 0.12)] = BUILT
    road = (bright < 0.118) & (ndvi < 0.25) & (nir < 0.17)
    cls[road] = ROAD
    cls[(bright > 0.27) & (ndvi < 0.2)] = VEHICLE
    cls[(ndwi > 0.15) & (nir < 0.10)] = WATER
    built_density = ndi.uniform_filter((cls == BUILT).astype(np.float32), 5)
    cls[(cls == ROAD) & (built_density > 0.3)] = BUILT
    veh = cls == VEHICLE
    if veh.any():
        lab, n = ndi.label(veh)
        sizes = ndi.sum(veh, lab, range(1, n + 1))
        big = np.isin(lab, np.where(sizes > 14)[0] + 1)
        cls[big] = BUILT
    if valid is not None:
        cls[~valid] = MASKED
    return cls

def to_rgb8(r: np.ndarray, valid: np.ndarray | None = None, gain: float = 1 / 0.30) -> np.ndarray:
    rgb = np.stack([r[2], r[1], r[0]], -1) * gain
    rgb = np.clip(rgb, 0, 1) ** 0.85
    out = (rgb * 255).astype(np.uint8)
    return out

def sar_to_rgb8(db: np.ndarray) -> np.ndarray:
    g = np.clip((db + 25) / 25, 0, 1)
    g = (g * 255).astype(np.uint8)
    return np.stack([g, g, g], -1)

def save_png(arr: np.ndarray, path: Path, scale: int = 1):
    img = Image.fromarray(arr)
    if scale != 1:
        img = img.resize((img.width * scale, img.height * scale), Image.NEAREST)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, optimize=True)
    return path

def class_rgb(cls: np.ndarray) -> np.ndarray:
    return CLASS_COLORS[cls]

def write_meta(path: Path, meta: dict):
    path.write_text(json.dumps(meta, indent=1, default=str))
