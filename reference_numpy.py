"""
Reference implementation (NumPy) of the "Deband-like" bit-depth expansion.
Same algorithm as the PyTorch node; used for testing and as spec for the AE port.

Idea: each quantized pixel value v represents a true value somewhere in
[v - q/2, v + q/2]. We estimate that true value by averaging neighbours that
are within `reach` quantization steps (edge-preserving), then clamp the
result so it never leaves the pixel's neighbourhood of bins -> detail is kept.
"""
import numpy as np

MODES = {
    #          samples, passes, flat_only
    "lite":     (12, 1, True),
    "standard": (24, 2, False),
    "super":    (64, 4, False),
}


def bilinear(img, y, x):
    """img: (C,H,W); y,x: (H,W) float coords. Edge-clamped bilinear sample."""
    C, H, W = img.shape
    x = np.clip(x, 0, W - 1); y = np.clip(y, 0, H - 1)
    x0 = np.floor(x).astype(int); y0 = np.floor(y).astype(int)
    x1 = np.minimum(x0 + 1, W - 1); y1 = np.minimum(y0 + 1, H - 1)
    fx = x - x0; fy = y - y0
    a = img[:, y0, x0]; b = img[:, y0, x1]; c = img[:, y1, x0]; d = img[:, y1, x1]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def local_range(img, r=2):
    """max-min over a (2r+1)^2 window, max across channels. (C,H,W)->(H,W)"""
    p = np.pad(img, ((0, 0), (r, r), (r, r)), mode="edge")
    H, W = img.shape[1:]
    mx = np.full(img.shape, -np.inf); mn = np.full(img.shape, np.inf)
    for dy in range(2 * r + 1):
        for dx in range(2 * r + 1):
            s = p[:, dy:dy + H, dx:dx + W]
            mx = np.maximum(mx, s); mn = np.minimum(mn, s)
    return (mx - mn).max(0)


def deband(img, bits=8, reach=2, spatial=24, mode="standard", seed=0):
    """img: (C,H,W) float in [0,1]. Returns float32 (C,H,W)."""
    samples, passes, flat_only = MODES[mode]
    q = 1.0 / (2 ** bits - 1)
    thr = reach * q                      # colour window for neighbours
    limit = 0.5 * reach * q              # max distance output may move
    rng = np.random.default_rng(seed)
    C, H, W = img.shape
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)

    orig = img.astype(np.float32)
    cur = orig.copy()
    golden = np.pi * (3 - np.sqrt(5))
    for p in range(passes):
        phi = rng.uniform(0, 2 * np.pi, (H, W))          # per-pixel rotation
        acc = cur.copy(); wsum = np.ones((H, W), np.float32)
        for k in range(samples):
            # Vogel spiral: uniform coverage of the disc of radius `spatial`
            r = spatial * np.sqrt((k + 0.5) / samples)
            a = k * golden + phi
            s_cur = bilinear(cur, yy + r * np.sin(a), xx + r * np.cos(a))
            s_org = bilinear(orig, yy + r * np.sin(a), xx + r * np.cos(a))
            diff = np.abs(s_org - orig).max(0)           # joint RGB test
            w = np.clip(1.0 - diff / (thr + 1e-8), 0, 1) # soft range weight
            acc += s_cur * w; wsum += w
        cur = acc / wsum
        cur = np.clip(cur, orig - limit, orig + limit)   # stay near own bin

    if flat_only:  # Lite: only touch low-gradient areas (skies, walls)
        m = np.clip(1.0 - (local_range(orig) - thr) / (thr + 1e-8), 0, 1)
        cur = orig + (cur - orig) * m[None]
    return cur.astype(np.float32)
