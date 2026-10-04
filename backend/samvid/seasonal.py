from __future__ import annotations
import math
from datetime import date
import numpy as np

FEATURES = ["ndvi_s", "ndwi_s", "ndbi_s", "blue_red", "ndvi"]
CHANGE_FEATURES = 4
SIGMA_FLOOR = np.array([0.03, 0.03, 0.03, 0.025, 0.035], np.float32)
ORIGIN = date(2024, 1, 1)

def t_months(d: str) -> float:
    y, m, dd = (int(x) for x in d.split("-"))
    return (y - ORIGIN.year) * 12 + (m - 1) + (dd - 1) / 30.0

def design(t: np.ndarray) -> np.ndarray:
    w = 2 * math.pi / 12.0
    t = np.asarray(t, np.float64)
    return np.stack([np.ones_like(t), np.cos(w * t), np.sin(w * t), np.cos(2 * w * t), np.sin(2 * w * t)], -1)

def feature_stack(norm: np.ndarray) -> np.ndarray:
    b, g, r, n, s = (norm[:, i] for i in range(5))
    k = 0.08
    return np.stack([(n - r) / (n + r + k), (g - n) / (g + n + k), (s - n) / (s + n + k),
                     (b - r) / (b + r + 0.1), (n - r) / (n + r + 1e-6)], 1).astype(np.float32)

def fit(feats: np.ndarray, valid: np.ndarray, dates: list[str], train_mask: np.ndarray, iters: int = 3):
    T, F, H, W = feats.shape
    X = design(np.array([t_months(d) for d in dates]))[train_mask]        # (Tt,5)
    Y = feats[train_mask].reshape(X.shape[0], F, H * W).astype(np.float64)  # (Tt,F,P)
    Wt = valid[train_mask].reshape(X.shape[0], 1, H * W).astype(np.float64)
    Wt = np.repeat(Wt, F, axis=1)
    coef = np.zeros((F, 5, H * W))
    ridge = np.diag([1e-6, 0.05, 0.05, 0.2, 0.2])   
    for _ in range(iters):
        for f in range(F):
            w = Wt[:, f]                                
            XtWX = np.einsum("ti,tp,tj->pij", X, w, X) + ridge
            XtWy = np.einsum("ti,tp,tp->pi", X, w, Y[:, f])
            coef[f] = np.linalg.solve(XtWX, XtWy[..., None])[..., 0].T
        pred = np.einsum("ti,fip->tfp", X, coef)
        res = Y - pred
        mad = np.median(np.abs(np.where(Wt > 0, res, np.nan)), axis=0)
        mad = np.nan_to_num(mad, nan=0.05)
        c = 4.685 * np.maximum(mad / 0.6745, SIGMA_FLOOR[:, None])
        u = res / c[None]
        tukey = np.where(np.abs(u) < 1, (1 - u ** 2) ** 2, 0.0)
        Wt = (valid[train_mask].reshape(X.shape[0], 1, H * W) > 0) * tukey
    pred = np.einsum("ti,fip->tfp", X, coef)
    vmask = np.repeat(valid[train_mask].reshape(X.shape[0], 1, H * W), F, axis=1)
    res = (Y - pred) * vmask
    n = np.maximum(vmask.sum(0), 1)
    sigma = np.sqrt((res ** 2).sum(0) / np.maximum(n - 2, 1))
    sigma = np.maximum(sigma, SIGMA_FLOOR[:, None])
    return coef.reshape(F, 5, H, W).astype(np.float32), sigma.reshape(F, H, W).astype(np.float32)

def coverage(valid: np.ndarray, dates: list[str], train_mask: np.ndarray) -> np.ndarray:
    H, W = valid.shape[1:]
    cov = np.zeros((12, H, W), np.uint8)
    for k, d in enumerate(dates):
        if not train_mask[k]:
            continue
        m = int(d[5:7]) - 1
        for dm in (-1, 0, 1):
            cov[(m + dm) % 12] += valid[k].astype(np.uint8)
    return cov

def support_factor(cov: np.ndarray, d: str) -> np.ndarray:
    c = cov[int(d[5:7]) - 1]
    return np.where(c == 0, 2.5, np.where(c == 1, 1.4, 1.0)).astype(np.float32)

def expected(coef: np.ndarray, d: str) -> np.ndarray:
    x = design(np.array([t_months(d)]))[0]
    return np.einsum("i,fihw->fhw", x, coef)

def monthly_tile_baseline(coef, sigma, tile_slices):
    out = {}
    for m in range(1, 13):
        e = expected(coef, f"2025-{m:02d}-15")[4]
        for tid, (ys, xs) in tile_slices.items():
            out.setdefault(tid, []).append({
                "month": m,
                "ndvi_expected": round(float(e[ys, xs].mean()), 3),
                "ndvi_sigma": round(float(sigma[4][ys, xs].mean()), 3),
            })
    return out
