from __future__ import annotations
import numpy as np
from scipy import ndimage as ndi
from . import config as cfg
from . import imagery as im

FEATURE_NAMES = (
    [f"frac_{c}" for c in ["water", "veg", "bare", "built", "road", "vehicle", "disturbed"]]
    + [f"mean_{b}" for b in ["B2", "B3", "B4", "B8", "B11"]]
    + [f"std_{b}" for b in ["B2", "B3", "B4", "B8", "B11"]]
    + [f"{i}_{s}" for i in ["ndvi", "ndwi", "ndbi", "br"] for s in ["mean", "std"]]
    + [f"ndvi_hist_{k}" for k in range(8)]
    + ["edge_density", "orientation_coherence", "local_var", "blob_count",
       "linear_frac", "built_compactness", "water_edge", "veg_patchiness"]
    + ["sar_mean_db", "sar_std_db", "sar_bright_frac", "sar_dark_frac"]
)
DIM = len(FEATURE_NAMES)
_W = np.ones(DIM, np.float32)
_W[:7] = 3.0
_W[25:33] = 0.6
_W[33:41] = 1.6

def spectral_texture(r: np.ndarray, cls: np.ndarray, valid: np.ndarray, sar: np.ndarray | None = None) -> np.ndarray:
    v = valid if valid.any() else np.ones_like(valid)
    feats = []
    n = v.sum()
    for c in range(7):
        feats.append(((cls == c) & v).sum() / n)
    for b in range(5):
        feats.append(float(r[b][v].mean()))
    for b in range(5):
        feats.append(float(r[b][v].std()))
    ix = im.indices(r)
    for k in ["ndvi", "ndwi", "ndbi", "br"]:
        a = np.clip(ix[k][v], -1, 3)
        feats += [float(a.mean()), float(a.std())]
    h, _ = np.histogram(np.clip(ix["ndvi"][v], -0.2, 0.8), bins=8, range=(-0.2, 0.8))
    feats += list(h / max(1, h.sum()))
    nir = np.where(v, r[3], r[3][v].mean())
    gx, gy = ndi.sobel(nir, 1), ndi.sobel(nir, 0)
    mag = np.hypot(gx, gy)
    feats.append(float((mag > 0.15).mean()))
    jxx, jyy, jxy = (gx * gx).mean(), (gy * gy).mean(), (gx * gy).mean()
    coh = np.sqrt((jxx - jyy) ** 2 + 4 * jxy ** 2) / (jxx + jyy + 1e-9)
    feats.append(float(coh))
    m1 = ndi.uniform_filter(nir, 3)
    feats.append(float(np.clip(ndi.uniform_filter(nir * nir, 3) - m1 * m1, 0, None).mean() * 100))
    veh = (cls == im.VEHICLE) & v
    feats.append(min(1.0, ndi.label(veh)[1] / 20.0))
    road = (cls == im.ROAD) & v
    feats.append(float(road.mean()))
    built = (cls == im.BUILT) & v
    if built.any():
        lab, nb = ndi.label(built)
        feats.append(float(built.sum() / max(1, nb) / built.size))
    else:
        feats.append(0.0)
    water = (cls == im.WATER) & v
    feats.append(float((ndi.binary_dilation(water) ^ water).mean() * 4))
    veg = (cls == im.VEG) & v
    feats.append(float(ndi.label(veg)[1] / 10.0))
    if sar is not None:
        feats += [float((sar.mean() + 25) / 25), float(sar.std() / 10),
                  float((sar > -6).mean()), float((sar < -18).mean())]
    else:
        feats += [0.0, 0.0, 0.0, 0.0]
    return np.asarray(feats, np.float32)

class SpectralTextureEmbedder:
    name = f"spectral-texture-{DIM}d (built-in)"
    dim = DIM

    def __init__(self):
        self.mu = None
        self.sd = None

    def fit_stats(self, raw: np.ndarray):
        self.mu = raw.mean(0)
        self.sd = raw.std(0) + 1e-3

    def embed(self, raw: np.ndarray) -> np.ndarray:
        x = (raw - self.mu) / self.sd * _W
        x = x / (np.linalg.norm(x, axis=-1, keepdims=True) + 1e-9)
        return x.astype(np.float32)

def get_embedder():
    want = cfg.EMBEDDER
    if want in ("auto", "remoteclip") and cfg.REMOTECLIP_WEIGHTS.exists():
        try:
            import open_clip  
        except ImportError:
            pass
    return SpectralTextureEmbedder()
