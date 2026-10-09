"""
Deband nodes for ComfyUI-Bruxos-MediaIO
  - Deband Bit-Depth Expand (Deband): edge-aware bit-depth expansion
  - Save Image 16-bit PNG: keeps the extra precision (default SaveImage is 8-bit)

Copy this file into ComfyUI-Bruxos-MediaIO/ and register it in __init__.py
(see LEIA-ME.txt).
"""
import math
import os
import struct
import zlib

import numpy as np
import torch
import torch.nn.functional as F

import folder_paths

CATEGORY = "Bruxos-MediaIO/image"

MODES = {
    #          samples, passes, flat_only
    "lite":     (12, 1, True),
    "standard": (24, 2, False),
    "super":    (64, 4, False),
}


# --------------------------------------------------------------------------- core
def _local_range(x, r=2):
    k = 2 * r + 1
    mx = F.max_pool2d(F.pad(x, (r, r, r, r), mode="replicate"), k, 1)
    mn = -F.max_pool2d(F.pad(-x, (r, r, r, r), mode="replicate"), k, 1)
    return (mx - mn).amax(1, keepdim=True)


def _hash_angle(H, W, seed, device):
    """Deterministic per-pixel angle (same idea as the AE plugin): stable across frames."""
    y = torch.arange(H, device=device, dtype=torch.int64).view(H, 1)
    x = torch.arange(W, device=device, dtype=torch.int64).view(1, W)
    M = 0xFFFFFFFF
    s = (int(seed) * 0xCB1AB31F) & M  # computed in Python: no int64 overflow
    h = (((x * 0x8DA6B343) & M) ^ ((y * 0xD8163841) & M) ^ s) & M
    h = h ^ (h >> 16); h = (h * 0x7FEB352D) & M
    h = h ^ (h >> 15); h = (h * 0x846CA68B) & M
    h = h ^ (h >> 16)
    return (h & 0xFFFFFF).float() * (2 * math.pi / 16777216.0)


@torch.no_grad()
def deband_expand(img, bits=8, reach=2, spatial=24, mode="standard", seed=0):
    """img: (B,C,H,W) float. Returns float32 (B,C,H,W)."""
    samples, passes, flat_only = MODES[mode]
    q = 1.0 / (2 ** bits - 1)
    thr = reach * q
    limit = 0.5 * reach * q
    B, C, H, W = img.shape
    dev = img.device

    orig = img.float()
    cur = orig.clone()
    ys, xs = torch.meshgrid(torch.arange(H, device=dev, dtype=torch.float32),
                            torch.arange(W, device=dev, dtype=torch.float32),
                            indexing="ij")
    golden = math.pi * (3 - math.sqrt(5))
    both = torch.cat([orig, cur], 1)  # sample orig + current in a single grid_sample

    for p in range(passes):
        both[:, C:] = cur
        phi = _hash_angle(H, W, seed * 131 + p * 7919, dev)
        acc = cur.clone()
        wsum = torch.ones((B, 1, H, W), device=dev)
        for k in range(samples):
            r = spatial * math.sqrt((k + 0.5) / samples)
            a = phi + k * golden
            gx = (xs + r * torch.cos(a)) / max(W - 1, 1) * 2 - 1
            gy = (ys + r * torch.sin(a)) / max(H - 1, 1) * 2 - 1
            grid = torch.stack([gx, gy], -1).unsqueeze(0).expand(B, -1, -1, -1)
            s = F.grid_sample(both, grid, mode="bilinear",
                              padding_mode="border", align_corners=True)
            diff = (s[:, :C] - orig).abs().amax(1, keepdim=True)
            w = (1.0 - diff / (thr + 1e-8)).clamp(0, 1)
            acc += s[:, C:] * w
            wsum += w
        cur = acc / wsum
        cur = torch.maximum(torch.minimum(cur, orig + limit), orig - limit)

    if flat_only:
        m = (1.0 - (_local_range(orig) - thr) / (thr + 1e-8)).clamp(0, 1)
        cur = orig + (cur - orig) * m
    return cur


# --------------------------------------------------------------------------- nodes
class BruxosDeband:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "image": ("IMAGE",),
            "source_bits": ("INT", {"default": 8, "min": 4, "max": 12,
                "tooltip": "Bit depth the footage was quantized to. For AI images, lower (6-7) = catches coarser banding."}),
            "spectral_reach": ("INT", {"default": 2, "min": 1, "max": 16,
                "tooltip": "How many quantization steps a pixel may interpolate across. Too high = detail loss."}),
            "spatial": ("INT", {"default": 24, "min": 2, "max": 256,
                "tooltip": "Neighbourhood radius in pixels. Use ~half the width of the bands."}),
            "mode": (list(MODES.keys()), {"default": "standard"}),
            "seed": ("INT", {"default": 0, "min": 0, "max": 2**31 - 1}),
        }}

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "run"
    CATEGORY = CATEGORY

    def run(self, image, source_bits, spectral_reach, spatial, mode, seed):
        import comfy.model_management as mm
        device = mm.get_torch_device()
        out = []
        for i in range(image.shape[0]):  # frame by frame: works for video batches, low VRAM
            x = image[i:i + 1, ..., :3].permute(0, 3, 1, 2).to(device)
            y = deband_expand(x, source_bits, spectral_reach, spatial, mode, seed)
            y = y.permute(0, 2, 3, 1).cpu()
            if image.shape[-1] > 3:  # keep alpha untouched
                y = torch.cat([y, image[i:i + 1, ..., 3:]], -1)
            out.append(y)
            mm.throw_exception_if_processing_interrupted()
        return (torch.cat(out, 0).clamp(0, 1),)


def write_png16(path, arr):
    """arr: HxWx3 or HxWx4 float [0,1] -> 16-bit PNG (no extra dependencies)."""
    h, w, c = arr.shape
    data = (np.clip(arr, 0, 1) * 65535.0 + 0.5).astype(">u2")
    raw = np.zeros((h, 1 + w * c * 2), np.uint8)
    raw[:, 1:] = data.reshape(h, -1).view(np.uint8)
    color_type = 6 if c == 4 else 2

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 16, color_type, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw.tobytes(), 6))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


class BruxosSaveImage16:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "images": ("IMAGE",),
            "filename_prefix": ("STRING", {"default": "deband/frame"}),
        }}

    RETURN_TYPES = ()
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = CATEGORY

    def save(self, images, filename_prefix):
        out_dir = folder_paths.get_output_directory()
        full_folder, filename, counter, _, _ = folder_paths.get_save_image_path(
            filename_prefix, out_dir, images.shape[2], images.shape[1])
        os.makedirs(full_folder, exist_ok=True)
        for img in images:
            name = f"{filename}_{counter:05}.png"
            write_png16(os.path.join(full_folder, name), img.cpu().numpy())
            counter += 1
        return {}


NODE_CLASS_MAPPINGS = {
    "BruxosDeband": BruxosDeband,
    "BruxosSaveImage16": BruxosSaveImage16,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "BruxosDeband": "Deband (Bit-Depth Expand)",
    "BruxosSaveImage16": "Save Image 16-bit PNG",
}
