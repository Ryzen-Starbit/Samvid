from __future__ import annotations
import numpy as np
from scipy import ndimage as ndi
from . import imagery as im

def cloud_snow_masks(r: np.ndarray):
    ix = im.indices(r)
    snow = (ix["ndsi"] > 0.4) & (r[1] > 0.3)
    cand = (ix["bright"] > 0.30) & (ix["ndsi"] < 0.4) & (ix["ndvi"] < 0.25)
    lab, n = ndi.label(cand)
    if n:
        sizes = ndi.sum(cand, lab, range(1, n + 1))
        cand = np.isin(lab, np.where(sizes >= 30)[0] + 1)  
    cloud = ndi.binary_dilation(cand, iterations=5) if cand.any() else cand
    snow = ndi.binary_dilation(snow, iterations=1) & ~cloud if snow.any() else snow
    return cloud, snow

def shadow_mask(r, ref, cloud, ref_water):
    if not cloud.any():
        return np.zeros(cloud.shape, bool)
    near = ndi.binary_dilation(cloud, iterations=22)
    dark = (r[3] < 0.62 * ref[3]) & ((r[0] + r[1] + r[2]) < 0.65 * (ref[0] + ref[1] + ref[2]))
    sh = dark & near & ~cloud & ~ref_water
    return ndi.binary_dilation(sh, iterations=2) & ~cloud

def _grad(a):
    a = ndi.gaussian_filter(a, 1.5)  
    gx = ndi.sobel(a, 1)
    gy = ndi.sobel(a, 0)
    return np.hypot(gx, gy)

def phase_correlation(a: np.ndarray, b: np.ndarray, weight: np.ndarray | None = None):
    a = _grad(a)
    b = _grad(b)
    if weight is not None:
        w = ndi.gaussian_filter(ndi.binary_erosion(weight, iterations=6).astype(np.float32), 4)
        a = (a - (a * w).sum() / (w.sum() + 1e-9)) * w
        b = (b - (b * w).sum() / (w.sum() + 1e-9)) * w
    a = (a - a.mean()) * np.hanning(a.shape[0])[:, None] * np.hanning(a.shape[1])[None]
    b = (b - b.mean()) * np.hanning(b.shape[0])[:, None] * np.hanning(b.shape[1])[None]
    fa, fb = np.fft.fft2(a), np.fft.fft2(b)
    rr = fa * np.conj(fb)
    rr /= np.abs(rr) + 1e-9
    corr = np.fft.ifft2(rr).real
    py, px = np.unravel_index(np.argmax(corr), corr.shape)
    h, w = corr.shape

    def sub(c, i, n, axis):
        im1 = corr[(i - 1) % n, px] if axis == 0 else corr[py, (i - 1) % n]
        ip1 = corr[(i + 1) % n, px] if axis == 0 else corr[py, (i + 1) % n]
        den = im1 - 2 * c + ip1
        return 0.0 if abs(den) < 1e-12 else 0.5 * (im1 - ip1) / den
    c0 = corr[py, px]
    dy = py + sub(c0, py, h, 0)
    dx = px + sub(c0, px, w, 1)
    if dy > h / 2:
        dy -= h
    if dx > w / 2:
        dx -= w
    return float(dx), float(dy), float(c0)

def coregister(r: np.ndarray, ref: np.ndarray, usable: np.ndarray):
    probe = np.where(usable, r[3], ref[3])
    dx, dy, peak = phase_correlation(probe, ref[3], usable)
    ix, iy = int(round(dx)), int(round(dy))
    corrected = r
    if ix or iy:   # undo the offset
        corrected = np.roll(np.roll(r, -iy, axis=1), -ix, axis=2)
    u2 = np.roll(np.roll(usable, -iy, 0), -ix, 1)
    probe2 = np.where(u2, corrected[3], ref[3])
    rdx, rdy, _ = phase_correlation(probe2, ref[3], u2)
    residual = float(np.hypot(rdx, rdy))
    return corrected, {"dx": round(dx, 2), "dy": round(dy, 2), "applied": [-ix, -iy],
                       "residual_px": round(residual, 2), "peak": round(peak, 3)}, (ix, iy)

def radiometric_normalise(r, ref, usable, pif):
    sel = usable & pif
    out = np.empty_like(r)
    fits = []
    for bnd in range(r.shape[0]):
        x = r[bnd][sel]
        y = ref[bnd][sel]
        if x.size < 200:
            out[bnd] = r[bnd]
            fits.append([1.0, 0.0])
            continue
        edges = np.quantile(y, np.linspace(0, 1, 11))
        bins = np.clip(np.searchsorted(edges, y, side="right") - 1, 0, 9)
        mx = np.array([np.median(x[bins == k]) for k in range(10) if (bins == k).sum() > 10])
        my = np.array([np.median(y[bins == k]) for k in range(10) if (bins == k).sum() > 10])
        if my.size >= 3 and (my.max() - my.min()) > 0.03:
            a, b = np.polyfit(mx, my, 1)
            if not (0.6 < a < 1.6) or abs(b) > 0.08:     
                lo_x, lo_y = np.quantile(x, 0.02), np.quantile(y, 0.02)
                hi_x, hi_y = np.median(x), np.median(y)
                a = float((hi_y - lo_y) / max(hi_x - lo_x, 1e-6))
                b = float(lo_y - a * lo_x)
        else:                                              
            lo_x, lo_y = np.quantile(x, 0.02), np.quantile(y, 0.02)
            hi_x, hi_y = np.median(x), np.median(y)
            if hi_x - lo_x > 0.01:
                a = float((hi_y - lo_y) / (hi_x - lo_x))
                b = float(lo_y - a * lo_x)
            else:
                a, b = float(hi_y / max(hi_x, 1e-6)), 0.0
        out[bnd] = r[bnd] * a + b
        fits.append([float(a), float(b)])
    if sel.sum() > 200:
        rb = np.median(r[0][sel]) / max(np.median(ref[0][sel]), 1e-6)
        rr = np.median(r[2][sel]) / max(np.median(ref[2][sel]), 1e-6)
        haze_ratio = float(rb / max(rr, 1e-6))
    else:
        haze_ratio = 1.0
    return out, {"gain_offset": [[round(a, 4), round(b, 4)] for a, b in fits],
                 "haze_detected": bool(haze_ratio > 1.06), "blue_red_excess": round(haze_ratio, 3)}
