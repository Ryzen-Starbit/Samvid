from __future__ import annotations
import json
import math
import shutil
import zlib
from dataclasses import dataclass, field, asdict
from datetime import date
import numpy as np
import rasterio
from rasterio.transform import from_origin
from scipy import ndimage as ndi
from . import config as cfg

N = cfg.SCENE_PX
DEG_PER_PX = 0.00009  
WATER, FOREST, CROP, BARE, BUILT, ROAD, VEHICLE, DISTURBED = range(8)
#                 B2     B3     B4     B8     B11
SPEC = {
    WATER:     [0.060, 0.060, 0.040, 0.020, 0.010],
    FOREST:    [0.030, 0.070, 0.040, 0.380, 0.170],
    BARE:      [0.130, 0.170, 0.230, 0.290, 0.340],
    BUILT:     [0.200, 0.210, 0.230, 0.250, 0.310],
    ROAD:      [0.080, 0.090, 0.100, 0.120, 0.140],
    VEHICLE:   [0.320, 0.330, 0.340, 0.360, 0.380],
    DISTURBED: [0.170, 0.200, 0.250, 0.290, 0.370],
}
CROP_GREEN = np.array([0.035, 0.080, 0.045, 0.420, 0.190])
CROP_FALLOW = np.array([0.100, 0.130, 0.170, 0.240, 0.300])
SNOW = np.array([0.850, 0.850, 0.830, 0.750, 0.070])
CLOUD = np.array([0.550, 0.560, 0.570, 0.600, 0.450])
BUILT_GAP = np.array([0.090, 0.090, 0.100, 0.120, 0.150])
SAR_DB = {WATER: -21.0, FOREST: -8.0, CROP: -12.0, BARE: -15.0, BUILT: -3.0,
          ROAD: -17.0, VEHICLE: 2.0, DISTURBED: -12.5}


@dataclass
class Event:
    id: str
    aoi: str
    type: str                 
    bbox: list               
    start_idx: int           
    end_idx: int              
    growth: str = "linear"    
    note: str = ""
    extra: dict = field(default_factory=dict)

@dataclass
class AOI:
    id: str
    name: str
    lat: float
    lon: float
    seed: int
    has_river: bool = False
    desert: bool = False
    snow: bool = False
    events: list = field(default_factory=list)

MONTHS = [(2023 + (6 + i) // 12, (6 + i) % 12 + 1) for i in range(36)]
HIST = 12

def crop_greenness(month: int, double: bool) -> float:
    g = 0.5 + 0.5 * math.cos(2 * math.pi * (month - 8.5) / 12)
    if double:
        g = max(g, 0.85 * (0.5 + 0.5 * math.cos(2 * math.pi * (month - 2) / 12)) ** 2)
    return float(np.clip(g, 0, 1))

def forest_greenness(month: int) -> float:
    return 0.85 + 0.15 * math.cos(2 * math.pi * (month - 9) / 12)

def _blob(rng, shape, scale, thresh):
    noise = ndi.gaussian_filter(rng.standard_normal(shape), scale)
    noise = (noise - noise.mean()) / (noise.std() + 1e-9)
    return noise > thresh

def _progress(ev: Event, k: int) -> float:
    if k < ev.start_idx:
        return 0.0
    if ev.growth == "abrupt":
        return 1.0
    span = max(1, ev.end_idx - ev.start_idx)
    t = min(1.0, (k - ev.start_idx + 1) / (span + 1))
    if ev.growth == "accelerating":
        return t ** 2.6
    return t

def build_base(aoi: AOI, rng):
    cls = np.full((N, N), CROP if not aoi.desert else BARE, dtype=np.uint8)
    crop_double = np.zeros((N, N), bool)
    yy, xx = np.mgrid[0:N, 0:N]
    if not aoi.desert:
        for _ in range(28):
            x0, y0 = rng.integers(0, N - 20, 2)
            w, h = rng.integers(14, 46, 2)
            choice = rng.random()
            sl = (slice(y0, y0 + h), slice(x0, x0 + w))
            if choice < 0.25:
                cls[sl] = BARE
            elif choice < 0.6:
                crop_double[sl] = True
        cls[_blob(rng, (N, N), 9, 1.0)] = FOREST
    else:
        cls[_blob(rng, (N, N), 11, 1.6)] = CROP 
    if aoi.has_river:
        centre = N * 0.55 + 22 * np.sin(yy / 37.0) + 9 * np.sin(yy / 13.0)
        river = np.abs(xx - centre) < (5 + 1.5 * np.sin(yy / 23.0))
        cls[river] = WATER
    sx, sy = rng.integers(10, 60, 2)
    cls[sy:sy + 30, sx:sx + 36] = BUILT
    cls[rng.integers(30, 220), :][...] = ROAD
    r = int(rng.integers(40, 220))
    cls[r:r + 2, :] = ROAD
    c = int(rng.integers(20, 90))
    cls[:, c:c + 2] = ROAD
    return cls, crop_double

def render_class_map(aoi: AOI, base, k):
    cls = base.copy()
    for ev in aoi.events:
        p = _progress(ev, k)
        x0, y0, x1, y1 = ev.bbox
        if ev.type == "construction" and p > 0:
            cls[y0:y1, x0:x1] = DISTURBED
            rows = max(1, int(round((y1 - y0) * p)))
            cls[y0:y0 + rows, x0:x1] = BUILT
        elif ev.type == "clearance" and p > 0:
            region = cls[y0:y1, x0:x1]
            region[(region == FOREST) | (region == CROP)] = BARE
        elif ev.type == "water_expansion":
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            r0, r1 = ev.extra["r0"], ev.extra["r1"]
            r = r0 + (r1 - r0) * p
            yy, xx = np.mgrid[0:N, 0:N]
            mask = ((xx - cx) / (r * 1.35)) ** 2 + ((yy - cy) / r) ** 2 < 1
            cls[mask] = WATER
        elif ev.type == "road_development" and p > 0:
            (ax, ay), (bx, by) = ev.extra["a"], ev.extra["b"]
            steps = int(max(abs(bx - ax), abs(by - ay)) * p)
            for s in range(steps + 1):
                t = s / max(1, int(max(abs(bx - ax), abs(by - ay))))
                px, py = int(ax + (bx - ax) * t), int(ay + (by - ay) * t)
                cls[max(0, py - 1):py + 1, max(0, px - 1):px + 1] = ROAD
        elif ev.type == "vehicle_concentration":
            if ev.start_idx <= k <= ev.end_idx:
                vr = np.random.default_rng(zlib.crc32(f"{ev.id}:{k}".encode()))
                for _ in range(ev.extra["count"]):
                    vx = int(vr.integers(x0, x1 - 3))
                    vy = int(vr.integers(y0, y1 - 2))
                    cls[vy:vy + 2, vx:vx + 3] = VEHICLE
    return cls

def render_optical(aoi, cls, crop_double, k, rng):
    year, month = MONTHS[k]
    img = np.zeros((5, N, N), np.float32)
    for c, s in SPEC.items():
        m = cls == c
        img[:, m] = np.array(s, np.float32)[:, None]
    if aoi.desert:
        img[:, cls == BARE] *= 1.15
    # crop phenology
    for dbl in (False, True):
        m = (cls == CROP) & (crop_double == dbl)
        g = crop_greenness(month, dbl)
        img[:, m] = (g * CROP_GREEN + (1 - g) * CROP_FALLOW)[:, None]
    fm = cls == FOREST
    g = forest_greenness(month)
    img[:, fm] = (g * np.array(SPEC[FOREST]) + (1 - g) * CROP_FALLOW)[:, None]
    yy, xx = np.mgrid[0:N, 0:N]
    gap = ((yy // 2 + xx // 3) % 3 == 0) & (cls == BUILT)
    img[:, gap] = BUILT_GAP[:, None]
    lowf = ndi.gaussian_filter(np.random.default_rng(aoi.seed + 7).standard_normal((N, N)), 6)
    img *= (1 + 0.6 * lowf / (np.abs(lowf).max() + 1e-9) * 0.12)[None].astype(np.float32)
    img *= 1 + 0.04 * rng.standard_normal((1, N, N)).astype(np.float32)
    img += 0.004 * rng.standard_normal((5, N, N)).astype(np.float32)
    qa = {"cloud": 0.0, "haze": False, "snow": 0.0, "shift": [0, 0]}
    if aoi.snow and month in (12, 1, 2):
        elev = ndi.gaussian_filter(np.random.default_rng(aoi.seed).standard_normal((N, N)), 25)
        elev = (elev - elev.min()) / (np.ptp(elev) + 1e-9) + (1 - (xx + yy) / (2 * N)) * 0.8
        snow = elev > np.quantile(elev, 0.55 if month == 1 else 0.7)
        img[:, snow] = SNOW[:, None] * (0.95 + 0.05 * rng.random())
        qa["snow"] = float(snow.mean())
    r = rng.random()
    if r < 0.26 or k in aoi.__dict__.get("forced_cloudy", ()):
        frac_t = 1.9 - 1.2 * rng.random()
        if k in aoi.__dict__.get("forced_cloudy", ()):
            frac_t = -0.9
        cloud = _blob(rng, (N, N), 14, frac_t)
        dens = np.clip(ndi.gaussian_filter(cloud.astype(np.float32), 2.5) * 1.3, 0, 1)
        shadow = np.roll(np.roll(cloud, 9, axis=0), 7, axis=1) & ~cloud
        img[:, shadow] *= 0.32
        img = img * (1 - dens) + CLOUD[:, None, None] * dens
        qa["cloud"] = float(cloud.mean())
    if rng.random() < 0.15:
        haze = 0.018 + 0.02 * rng.random()
        img += np.array([haze * 1.4, haze * 1.1, haze * 0.9, haze * 0.5, haze * 0.2], np.float32)[:, None, None]
        img *= 0.92
        qa["haze"] = True
    gain = 0.9 + 0.2 * rng.random()
    img = img * gain + (rng.random() - 0.5) * 0.02
    qa["gain"] = round(float(gain), 3)
    if rng.random() < 0.13:
        dx, dy = (int(v) for v in rng.integers(-3, 4, 2))
        if dx or dy:
            img = np.roll(np.roll(img, dy, axis=1), dx, axis=2)
            qa["shift"] = [dx, dy]
    return np.clip(img * 10000, 0, 65535).astype(np.uint16), qa

def render_sar(cls, crop_double, k, rng):
    _, month = MONTHS[k]
    db = np.zeros((N, N), np.float32)
    for c, v in SAR_DB.items():
        db[cls == c] = v
    db[cls == CROP] += 4.0 * crop_greenness(month, False)
    lin = 10 ** (db / 10)
    speckle = rng.gamma(4.0, 1 / 4.0, (N, N)).astype(np.float32)
    return (10 * np.log10(lin * speckle + 1e-6)).astype(np.float32)

def write_cog(path, arr, transform, dtype, nodata=None, tags=None):
    count = arr.shape[0] if arr.ndim == 3 else 1
    data = arr if arr.ndim == 3 else arr[None]
    tmp = path.with_suffix(".tmp.tif")
    with rasterio.open(tmp, "w", driver="GTiff", width=N, height=N, count=count,
                       dtype=dtype, crs="EPSG:4326", transform=transform,
                       compress="deflate", nodata=nodata) as dst:
        dst.write(data)
        if tags:
            dst.update_tags(**{k: json.dumps(v) if not isinstance(v, str) else v for k, v in tags.items()})
    with rasterio.open(tmp) as src:
        profile = src.profile.copy()
        profile.update(driver="COG", compress="deflate", blocksize=128)
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(src.read())
            dst.update_tags(**src.tags())
    tmp.unlink()

def define_aois():
    a1 = AOI("AOI-RIV", "Riverine Sector North", 26.112, 83.405, 11, has_river=True)
    a2 = AOI("AOI-RES", "Reservoir Sector", 23.806, 77.512, 22)
    a3 = AOI("AOI-PLN", "Plains Logistics Sector", 27.503, 74.208, 33, desert=True)
    a4 = AOI("AOI-HIL", "Highland Sector", 34.104, 75.006, 44, snow=True)
    a1.forced_cloudy = (25,)
    a2.forced_cloudy = (19,)
    a3.forced_cloudy = (28,)
    a4.forced_cloudy = (31,)
    E = Event
    a1.events = [
        E("EV-RIV-01", a1.id, "construction", [118, 40, 138, 58], 21, 34, "accelerating", "new compound on river bank"),
        E("EV-RIV-02", a1.id, "construction", [100, 170, 118, 186], 24, 35, "accelerating", "structures near river, second site"),
        E("EV-RIV-03", a1.id, "clearance", [20, 180, 60, 230], 20, 20, "abrupt", "vegetation cleared"),
        E("EV-RIV-04", a1.id, "vehicle_concentration", [168, 200, 210, 236], 27, 29, "transient", "vehicles on open ground near river", {"count": 26}),
    ]
    a2.events = [
        E("EV-RES-01", a2.id, "water_expansion", [70, 80, 170, 160], 18, 30, "linear", "reservoir filling", {"r0": 18, "r1": 34}),
        E("EV-RES-02", a2.id, "clearance", [180, 20, 236, 70], 23, 23, "abrupt", "forest clearance"),
        E("EV-RES-03", a2.id, "road_development", [20, 200, 240, 204], 16, 28, "linear", "new access road", {"a": [20, 202], "b": [238, 186]}),
        E("EV-RES-04", a2.id, "construction", [200, 120, 220, 136], 15, 22, "linear", "settlement expansion (completed early)"),
    ]
    a3.events = [
        E("EV-PLN-01", a3.id, "construction", [150, 30, 172, 50], 22, 35, "accelerating", "facility on open ground"),
        E("EV-PLN-02", a3.id, "construction", [60, 150, 80, 168], 25, 35, "accelerating", "facility on open ground"),
        E("EV-PLN-03", a3.id, "construction", [200, 180, 222, 198], 23, 35, "accelerating", "facility on open ground"),
        E("EV-PLN-04", a3.id, "vehicle_concentration", [120, 100, 170, 140], 30, 32, "transient", "large vehicle concentration", {"count": 40}),
        E("EV-PLN-05", a3.id, "vehicle_concentration", [20, 210, 60, 240], 18, 19, "transient", "vehicle concentration", {"count": 18}),
        E("EV-PLN-06", a3.id, "road_development", [100, 60, 104, 250], 20, 32, "accelerating", "track to facility", {"a": [102, 250], "b": [160, 52]}),
    ]
    a4.events = [
        E("EV-HIL-01", a4.id, "road_development", [10, 120, 250, 124], 14, 26, "linear", "mountain road", {"a": [10, 150], "b": [246, 100]}),
        E("EV-HIL-02", a4.id, "construction", [180, 190, 198, 206], 17, 25, "linear", "small camp (completed)"),
    ]
    return [a1, a2, a3, a4]

def generate(clean: bool = True, verbose: bool = True):
    if clean and cfg.ARCHIVE_DIR.exists():
        shutil.rmtree(cfg.ARCHIVE_DIR)
    cfg.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    truth = {"generated": date.today().isoformat(), "months": [f"{y}-{m:02d}" for y, m in MONTHS],
             "aois": [], "events": [], "scene_qa": {}}
    for aoi in define_aois():
        rng = np.random.default_rng(aoi.seed)
        base, crop_double = build_base(aoi, rng)
        transform = from_origin(aoi.lon - N / 2 * DEG_PER_PX, aoi.lat + N / 2 * DEG_PER_PX, DEG_PER_PX, DEG_PER_PX)
        out = cfg.ARCHIVE_DIR / aoi.id
        out.mkdir(parents=True, exist_ok=True)
        truth["aois"].append({"id": aoi.id, "name": aoi.name, "lat": aoi.lat, "lon": aoi.lon,
                              "bounds": [aoi.lon - N / 2 * DEG_PER_PX, aoi.lat - N / 2 * DEG_PER_PX,
                                         aoi.lon + N / 2 * DEG_PER_PX, aoi.lat + N / 2 * DEG_PER_PX]})
        truth["events"] += [asdict(e) for e in aoi.events]
        for k, (y, m) in enumerate(MONTHS):
            day = int(rng.integers(4, 26))
            d = f"{y}-{m:02d}-{day:02d}"
            cls = render_class_map(aoi, base, k)
            img, qa = render_optical(aoi, cls, crop_double, k, rng)
            name = f"S2_{aoi.id}_{d.replace('-', '')}.tif"
            write_cog(out / name, img, transform, "uint16",
                      tags={"SENSOR": "Sentinel-2 MSI (synthetic)", "ACQ_DATE": d,
                            "BANDS": "B2,B3,B4,B8,B11", "SCALE": "10000"})
            truth["scene_qa"][name] = qa
            if k % 2 == 0:
                sd = f"{y}-{m:02d}-{min(28, day + 2):02d}"
                sar = render_sar(cls, crop_double, k, rng)
                sname = f"S1_{aoi.id}_{sd.replace('-', '')}.tif"
                write_cog(out / sname, sar, transform, "float32",
                          tags={"SENSOR": "Sentinel-1 C-SAR VV (synthetic)", "ACQ_DATE": sd,
                                "BANDS": "VV_dB"})
        if verbose:
            print(f"  {aoi.id}: {len(MONTHS)} optical + {len(MONTHS) // 2} SAR scenes")
    cfg.GROUND_TRUTH_PATH.write_text(json.dumps(truth, indent=1))
    return truth

if __name__ == "__main__":
    print("Generating synthetic archive ->", cfg.ARCHIVE_DIR)
    generate()
    print("done")
