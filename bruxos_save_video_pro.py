# -*- coding: utf-8 -*-
"""Save Video Pro (Bruxos) -- saida final de VFX num node so.

  * player frame a frame no proprio node (proxy H.264 all-intra = seek exato)
  * seed da geracao: detectada sozinha no workflow (ou manual), vai pro nome,
    pros metadados do arquivo, pro sidecar .json e (opcional) queimada no frame
  * film grain embutido com presets de negativo (algoritmo do 😺NKD Film Grain,
    MIT (c) 2026 Nekodificador -- ver THIRD_PARTY_NOTICES.md)
  * formatos: mp4 h264 / h265 10-bit, ProRes 422 HQ / 4444, EXR (half/float)
    e PNG 16-bit
  * espacos de cor: sRGB, Linear Rec.709, ACEScg, ACES2065-1, ACEScct e
    ARRI LogC4 / AWG4 (matrizes e curvas conferidas contra o OCIO 2.x)

Fluxo: images -> pingpong -> grain (no espaco de exibicao, como no node NKD)
-> burn-in -> [preview sRGB] + [master no espaco de cor escolhido].
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction

import numpy as np
import torch
import torch.nn.functional as F

try:
    import folder_paths
    _HAS_FP = True
except Exception:  # pragma: no cover - fora do ComfyUI
    folder_paths = None
    _HAS_FP = False

try:
    import av
    _HAS_AV = True
except Exception:  # pragma: no cover
    av = None
    _HAS_AV = False

try:
    from . import video_nodes as _video  # _mux_audio
except Exception:  # pragma: no cover - teste isolado
    _video = None

TAG = "[Save Video Pro Bruxos]"

# ===========================================================================
# Formatos
# ===========================================================================
FORMATS = {
    "mp4 / h264": dict(kind="video", ext="mp4", codec="libx264", pix_fmt="yuv420p", bits=8),
    "mp4 / h265 10-bit": dict(kind="video", ext="mp4", codec="libx265", pix_fmt="yuv420p10le", bits=10),
    "mov / ProRes 422 HQ": dict(kind="video", ext="mov", codec="prores_ks", pix_fmt="yuv422p10le", bits=10, profile="3"),
    "mov / ProRes 4444": dict(kind="video", ext="mov", codec="prores_ks", pix_fmt="yuv444p10le", bits=10, profile="4"),
    "EXR sequence (half 16f)": dict(kind="exr", ext="exr", half=True),
    "EXR sequence (float 32f)": dict(kind="exr", ext="exr", half=False),
    "PNG 16-bit sequence": dict(kind="png", ext="png"),
}

COLOR_SPACES = [
    "sRGB (display, sem conversão)",
    "Linear Rec.709",
    "ACEScg (AP1 linear)",
    "ACES2065-1 (AP0 linear)",
    "ACEScct (AP1 log)",
    "ARRI LogC4 (AWG4)",
    "ARRI LogC3 (AWG3, EI800)",
]
_LINEAR_SPACES = {"Linear Rec.709", "ACEScg (AP1 linear)", "ACES2065-1 (AP0 linear)"}

INPUT_INTERP = [
    "sRGB → linear (padrão)",
    "Gamma 2.4 (Rec.1886) → linear",
    "ACES 2.0 SDR inverso (OCIO, recupera altas)",
]

EXR_COMPRESSION = ["ZIP", "PIZ", "DWAA", "ZIPS", "none"]

# Entrada HDR (LTX-2 HDR): o VAE decode do LTX entrega CODIGOS LOG, nao sRGB.
HDR_INPUTS = [
    "off (imagem sRGB normal)",
    "LTX HDR · ACEScct (saída do VAE Decode)",
    "LTX HDR · LogC3 (legado, saída do VAE Decode)",
    "ACEScg linear (hdr_linear do LTX ACEScct)",
    "Rec.709 linear (hdr_linear do LTX LogC3)",
]
HDR_VIEWS = [
    "ACES 2.0 SDR (OCIO)",            # LUT 3D 129^3 gerada pelo OCIO, aplicada na GPU (rapido)
    "Reinhard (igual ao LTX)",
    "clip (sem tonemap)",
    "ACES 2.0 SDR (OCIO exato, lento)",  # OCIO na CPU pixel a pixel (segundos por frame em 4K)
]
ACES_LUT_N = 129
_CCT_MAX = (np.log2(65504.0) + 9.72) / 17.52  # ACEScct do maior half-float
HDR_MAX = 1e4  # mesmo teto do LTXVHDRDecodePostprocess

# ===========================================================================
# Cor -- matrizes derivadas das cromaticidades (Bradford D65->D60, igual ao
# OCIO/ACES). Conferidas: AP1 bate com o builtin do OCIO a 1e-6, ACEScct a
# 1e-5 e a curva LogC4 bate exata (cinza 18% -> 0.2784).
# ===========================================================================
def _xy2XYZ(x, y):
    return np.array([x / y, 1.0, (1.0 - x - y) / y])


def _rgb2xyz(prims, white):
    P = np.stack([_xy2XYZ(*c) for c in prims], 1)
    return P * np.linalg.solve(P, _xy2XYZ(*white))


_BRADFORD = np.array([[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]])


def _mat(src, sw, dst, dw):
    M = _rgb2xyz(src, sw)
    if sw != dw:
        a = _BRADFORD @ _xy2XYZ(*sw)
        b = _BRADFORD @ _xy2XYZ(*dw)
        M = np.linalg.inv(_BRADFORD) @ np.diag(b / a) @ _BRADFORD @ M
    return np.linalg.inv(_rgb2xyz(dst, dw)) @ M


_D65, _D60 = (0.3127, 0.3290), (0.32168, 0.33767)
_R709 = [(0.64, 0.33), (0.30, 0.60), (0.15, 0.06)]
_AP0 = [(0.7347, 0.2653), (0.0, 1.0), (0.0001, -0.0770)]
_AP1 = [(0.713, 0.293), (0.165, 0.830), (0.128, 0.044)]
_AWG4 = [(0.7347, 0.2653), (0.1424, 0.8576), (0.0991, -0.0308)]
_AWG3 = [(0.6840, 0.3130), (0.2210, 0.8480), (0.0861, -0.1020)]

M_709_TO_AP0 = _mat(_R709, _D65, _AP0, _D60)
M_709_TO_AP1 = _mat(_R709, _D65, _AP1, _D60)
M_709_TO_AWG4 = _mat(_R709, _D65, _AWG4, _D65)
M_AP0_TO_709 = np.linalg.inv(M_709_TO_AP0)
M_709_TO_AWG3 = _mat(_R709, _D65, _AWG3, _D65)
M_AP1_TO_709 = np.linalg.inv(M_709_TO_AP1)
M_AP1_TO_AP0 = M_709_TO_AP0 @ M_AP1_TO_709
# ACES (D60) <-> ARRI Wide Gamut (D65): a ARRI/OCIO usam CAT02 (nao Bradford) --
# conferido contra os builtins ARRI do OCIO a 1e-6.
_CAT02 = np.array([[0.7328, 0.4296, -0.1624], [-0.7036, 1.6975, 0.0061], [0.0030, 0.0136, 0.9834]])


def _mat_cat(src, sw, dst, dw, cat):
    M = _rgb2xyz(src, sw)
    a, b = cat @ _xy2XYZ(*sw), cat @ _xy2XYZ(*dw)
    return np.linalg.inv(_rgb2xyz(dst, dw)) @ np.linalg.inv(cat) @ np.diag(b / a) @ cat @ M


M_AP1_TO_AWG4 = _mat_cat(_AP1, _D60, _AWG4, _D65, _CAT02)
M_AP1_TO_AWG3 = _mat_cat(_AP1, _D60, _AWG3, _D65, _CAT02)

# cromaticidades (EXR header): rx ry gx gy bx by wx wy
_CHROMA = {
    "Linear Rec.709": (0.64, 0.33, 0.30, 0.60, 0.15, 0.06, 0.3127, 0.3290),
    "ACEScg (AP1 linear)": (0.713, 0.293, 0.165, 0.830, 0.128, 0.044, 0.32168, 0.33767),
    "ACES2065-1 (AP0 linear)": (0.7347, 0.2653, 0.0, 1.0, 0.0001, -0.0770, 0.32168, 0.33767),
    "ARRI LogC4 (AWG4)": (0.7347, 0.2653, 0.1424, 0.8576, 0.0991, -0.0308, 0.3127, 0.3290),
    "ARRI LogC3 (AWG3, EI800)": (0.6840, 0.3130, 0.2210, 0.8480, 0.0861, -0.1020, 0.3127, 0.3290),
}
# tag 'colorSpace' no EXR (mesmo atributo que o LTX/OpenImageIO escreve)
_EXR_TAG = {
    "Linear Rec.709": "sRGB", "ACEScg (AP1 linear)": "ACEScg", "ACES2065-1 (AP0 linear)": "ACES2065-1",
    "ACEScct (AP1 log)": "ACEScct", "ARRI LogC4 (AWG4)": "ARRI LogC4", "ARRI LogC3 (AWG3, EI800)": "ARRI LogC3",
}
# nome que Nuke/Resolve/OCIO reconhecem
_OCIO_NAME = {
    "sRGB (display, sem conversão)": "sRGB - Display",
    "Linear Rec.709": "Linear Rec.709 (sRGB)",
    "ACEScg (AP1 linear)": "ACEScg",
    "ACES2065-1 (AP0 linear)": "ACES2065-1",
    "ACEScct (AP1 log)": "ACEScct",
    "ARRI LogC4 (AWG4)": "ARRI LogC4",
    "ARRI LogC3 (AWG3, EI800)": "ARRI LogC3 (EI800)",
}

_LC4_A = (2.0 ** 18 - 16.0) / 117.45
_LC4_B = (1023.0 - 95.0) / 1023.0
_LC4_C = 95.0 / 1023.0
_LC4_S = (7.0 * np.log(2.0) * 2.0 ** (7.0 - 14.0 * _LC4_C / _LC4_B)) / (_LC4_A * _LC4_B)
_LC4_T = (2.0 ** (14.0 * (-_LC4_C / _LC4_B) + 6.0) - 64.0) / _LC4_A


def _logc4(x: torch.Tensor) -> torch.Tensor:
    hi = (torch.log2((_LC4_A * x + 64.0).clamp_min(1e-10)) - 6.0) / 14.0 * _LC4_B + _LC4_C
    lo = (x - _LC4_T) / _LC4_S
    return torch.where(x >= _LC4_T, hi, lo)


def _acescct(x: torch.Tensor) -> torch.Tensor:
    hi = (torch.log2(x.clamp_min(1e-10)) + 9.72) / 17.52
    lo = 10.5402377416545 * x + 0.0729055341958355
    return torch.where(x <= 0.0078125, lo, hi)


# ARRI LogC3 EI800 (mesmas constantes do LTX hdr_nodes.LogC3)
_LC3 = dict(A=5.555556, B=0.052272, C=0.247190, D=0.385537, E=5.367655, F=0.092809, CUT=0.010591)


def _logc3(x: torch.Tensor) -> torch.Tensor:
    k = _LC3
    hi = k["C"] * torch.log10((k["A"] * x + k["B"]).clamp_min(1e-10)) + k["D"]
    lo = k["E"] * x + k["F"]
    return torch.where(x >= k["CUT"], hi, lo)


def _logc3_inv(t: torch.Tensor) -> torch.Tensor:
    k = _LC3
    cut_log = k["E"] * k["CUT"] + k["F"]
    hi = (torch.pow(10.0, (t - k["D"]) / k["C"]) - k["B"]) / k["A"]
    lo = (t - k["F"]) / k["E"]
    return torch.where(t >= cut_log, hi, lo)


def _acescct_inv(ct: torch.Tensor) -> torch.Tensor:
    hi = torch.pow(2.0, ct * 17.52 - 9.72)
    lo = (ct - 0.0729055341958355) / 10.5402377416545
    return torch.where(ct > 0.155251141552511, hi, lo)


def _lin_to_srgb(x: torch.Tensor) -> torch.Tensor:
    x = x.clamp(0.0, 1.0)
    return torch.where(x <= 0.0031308, 12.92 * x, 1.055 * x.clamp_min(0.0031308) ** (1 / 2.4) - 0.055).clamp(0.0, 1.0)


def _srgb_to_lin(x: torch.Tensor) -> torch.Tensor:
    return torch.where(x <= 0.04045, x / 12.92, ((x.clamp_min(0) + 0.055) / 1.055) ** 2.4)


def _apply_mat(x: torch.Tensor, M) -> torch.Tensor:
    m = torch.as_tensor(np.asarray(M, dtype=np.float32), device=x.device, dtype=x.dtype)
    return torch.einsum("...c,dc->...d", x, m)


_OCIO_INV_CACHE = {}
ACES_INV_HIGHLIGHT_CAP = 16.0  # branco 1.0 viraria ~128 linear; teto pra nao explodir


def _aces_inverse_view(x: torch.Tensor) -> torch.Tensor:
    """sRGB display (ACES 2.0 SDR 100nit) -> ACES2065-1 via OCIO builtins."""
    import PyOpenColorIO as OCIO  # noqa: N814
    proc = _OCIO_INV_CACHE.get("p")
    if proc is None:
        cfg = OCIO.Config.CreateRaw()
        grp = OCIO.GroupTransform()
        for name in ("DISPLAY - CIE-XYZ-D65_to_sRGB",
                     "ACES-OUTPUT - ACES2065-1_to_CIE-XYZ-D65 - SDR-100nit-REC709_2.0"):
            t = OCIO.BuiltinTransform(name)
            t.setDirection(OCIO.TRANSFORM_DIR_INVERSE)
            grp.appendTransform(t)
        proc = cfg.getProcessor(grp).getDefaultCPUProcessor()
        _OCIO_INV_CACHE["p"] = proc
    arr = np.ascontiguousarray(x.detach().float().cpu().numpy().clip(0.0, 1.0), dtype=np.float32)
    flat = arr.reshape(-1, 3)
    proc.applyRGB(flat)
    out = torch.from_numpy(flat.reshape(arr.shape)).to(x.device)
    return out.clamp(-0.0, ACES_INV_HIGHLIGHT_CAP)


def hdr_to_ap1(x: torch.Tensor, mode: str) -> torch.Tensor:
    """Entrada HDR do LTX -> ACEScg (AP1) scene-linear. Mesma matematica do
    LTXVHDRDecodePostprocess (decompress_acescct / LogC3.decompress, teto 1e4)."""
    x = x.float()
    if "ACEScct" in mode and "LTX HDR" in mode:
        lin = _acescct_inv(x.clamp(0.0, 1.0))            # ja e ACEScg
    elif "LogC3" in mode and "LTX HDR" in mode:
        lin709 = _logc3_inv(x.clamp(0.0, 1.0))           # LTX trata como Rec.709 linear
        lin = _apply_mat(lin709.clamp(0.0, HDR_MAX), M_709_TO_AP1)
    elif mode.startswith("ACEScg linear"):
        lin = x
    elif mode.startswith("Rec.709 linear"):
        lin = _apply_mat(x.clamp_min(0.0), M_709_TO_AP1)
    else:
        raise ValueError(f"hdr_input desconhecido: {mode}")
    # sem cortar o pe abaixo de zero aqui: o ACEScct de saida volta identico ao
    # codigo do VAE; os masters LINEARES cortam em 0 (como o LTX) em ap1_to_target
    return lin.clamp(-1.0, HDR_MAX)


def _native_is_709(mode: str) -> bool:
    return "LogC3" in mode or mode.startswith("Rec.709")


_OCIO_FWD_CACHE = {}


def _aces_view(ap1: torch.Tensor) -> torch.Tensor:
    """ACEScg -> sRGB display pela view ACES 2.0 SDR 100nit (OCIO builtins)."""
    import PyOpenColorIO as OCIO  # noqa: N814
    proc = _OCIO_FWD_CACHE.get("p")
    if proc is None:
        cfg = OCIO.Config.CreateRaw()
        grp = OCIO.GroupTransform()
        for name in ("ACES-OUTPUT - ACES2065-1_to_CIE-XYZ-D65 - SDR-100nit-REC709_2.0",
                     "DISPLAY - CIE-XYZ-D65_to_sRGB"):
            grp.appendTransform(OCIO.BuiltinTransform(name))
        proc = cfg.getProcessor(grp).getDefaultCPUProcessor()
        _OCIO_FWD_CACHE["p"] = proc
    ap0 = _apply_mat(ap1, M_AP1_TO_AP0)
    arr = np.ascontiguousarray(ap0.detach().float().cpu().numpy(), dtype=np.float32)
    flat = arr.reshape(-1, 3)
    proc.applyRGB(flat)
    return torch.from_numpy(flat.reshape(arr.shape)).to(ap1.device).clamp(0.0, 1.0)


_ACES_LUT = {}


def _aces_lut(dev):
    """LUT 3D da view ACES 2.0 SDR: grade em ACEScct (0..max half) -> sRGB, calculada
    UMA vez pelo proprio OCIO. Erro medio 0.07/255 vs o OCIO exato (p99 ~1/255;
    so cores extremamente saturadas fora do gamut passam disso)."""
    lut = _ACES_LUT.get(str(dev))
    if lut is None:
        base = _ACES_LUT.get("cpu")
        if base is None:
            c = torch.linspace(0.0, _CCT_MAX, ACES_LUT_N)
            r, g, b = torch.meshgrid(c, c, c, indexing="ij")
            ap1 = _acescct_inv(torch.stack([r, g, b], -1).reshape(1, -1, 3))
            base = _aces_view(ap1).reshape(ACES_LUT_N, ACES_LUT_N, ACES_LUT_N, 3)
            base = base.permute(3, 0, 1, 2).unsqueeze(0).contiguous()  # [1,3,R,G,B]
            _ACES_LUT["cpu"] = base
        lut = base.to(dev)
        _ACES_LUT[str(dev)] = lut
    return lut


def _aces_view_lut(ap1: torch.Tensor) -> torch.Tensor:
    sh = ap1.shape
    u = (_acescct(ap1.reshape(-1, 3)).clamp(0.0, _CCT_MAX) / _CCT_MAX) * 2.0 - 1.0
    grid = torch.stack([u[:, 2], u[:, 1], u[:, 0]], -1).view(1, 1, 1, -1, 3)
    out = F.grid_sample(_aces_lut(ap1.device), grid, mode="bilinear", align_corners=True)
    return out.view(3, -1).t().reshape(sh).clamp(0.0, 1.0)


def render_hdr_view(ap1: torch.Tensor, view: str, exposure: float, mode: str = "") -> torch.Tensor:
    """ACEScg scene-linear -> sRGB 0..1 pra ver (player, review, saida images)."""
    e = ap1.clamp_min(0.0) * (2.0 ** float(exposure))
    if view.startswith("ACES"):
        try:
            if "exato" in view:
                return _aces_view(e)
            return _aces_view_lut(e)
        except Exception as ex:
            logging.warning(f"{TAG} OCIO indisponivel p/ view ACES ({ex}); usando Reinhard.")
            view = "Reinhard"
    if view.startswith("Reinhard"):
        # identico ao LTX: Reinhard por canal no buffer linear NATIVO da entrada
        # (ACEScg pro ACEScct; Rec.709 pro LogC3) + OETF sRGB
        if _native_is_709(mode):
            e = _apply_mat(e, M_AP1_TO_709).clamp_min(0.0)
        return _lin_to_srgb((e / (1.0 + e)).clamp(0.0, 1.0))
    return _lin_to_srgb(_apply_mat(e, M_AP1_TO_709))


def ap1_to_target(ap1: torch.Tensor, space: str) -> torch.Tensor:
    """ACEScg scene-linear -> espaco do master (sem cortar as altas)."""
    if space.startswith("ACEScg"):
        return ap1.clamp_min(0.0)
    if space == "Linear Rec.709":
        return _apply_mat(ap1.clamp_min(0.0), M_AP1_TO_709).clamp_min(0.0)
    if space.startswith("ACES2065"):
        return _apply_mat(ap1.clamp_min(0.0), M_AP1_TO_AP0).clamp_min(0.0)
    if space.startswith("ACEScct"):
        return _acescct(ap1)
    if space.startswith("ARRI LogC4"):
        return _logc4(_apply_mat(ap1, M_AP1_TO_AWG4))
    if space.startswith("ARRI LogC3"):
        return _logc3(_apply_mat(ap1, M_AP1_TO_AWG3))
    raise ValueError(space)


def display_to_target(x: torch.Tensor, space: str, interp: str) -> torch.Tensor:
    """x: [...,3] display-referred 0..1  ->  float no espaco pedido."""
    if space.startswith("sRGB"):
        return x
    if interp.startswith("ACES 2.0"):
        try:
            ap0 = _aces_inverse_view(x)
            lin709 = _apply_mat(ap0, M_AP0_TO_709)
        except Exception as e:
            logging.warning(f"{TAG} OCIO indisponivel p/ ACES inverso ({e}); usando sRGB -> linear.")
            lin709 = _srgb_to_lin(x)
    elif interp.startswith("Gamma 2.4"):
        lin709 = x.clamp_min(0) ** 2.4
    else:
        lin709 = _srgb_to_lin(x)

    if space == "Linear Rec.709":
        return lin709
    if space.startswith("ACEScg"):
        return _apply_mat(lin709, M_709_TO_AP1)
    if space.startswith("ACES2065"):
        return _apply_mat(lin709, M_709_TO_AP0)
    if space.startswith("ACEScct"):
        return _acescct(_apply_mat(lin709, M_709_TO_AP1))
    if space.startswith("ARRI LogC4"):
        return _logc4(_apply_mat(lin709, M_709_TO_AWG4))
    if space.startswith("ARRI LogC3"):
        return _logc3(_apply_mat(lin709, M_709_TO_AWG3))
    return x


# ===========================================================================
# Film grain -- adaptado do 😺NKD Film Grain (MIT, Nekodificador).
# Mesma escala dos sliders do node NKD: os valores que voce ja usa la valem aqui.
# ===========================================================================
_GRAIN_MAX_AMOUNT = 0.22
_GRAIN_SIZE_SCALE_MAX = 7.0
_GRAIN_COARSE_MULT = 2.6
_GRAIN_ROUGH_MAX = 0.6
_GRAIN_CHANNEL_W = (2.0, 1.0, 3.0)
_GRAIN_FRAME_CHUNK = 8

# (amount, size, roughness, color) -- mesma escala 0..100 do NKD
GRAIN_PRESETS = {
    "off": None,
    "Digital fino (sutil)": (3.0, 15.0, 30.0, 0.0),
    "35mm Kodak 5203 (50D)": (4.0, 18.0, 35.0, 5.0),
    "35mm Kodak 5207 (250D)": (6.0, 22.0, 45.0, 8.0),
    "35mm Kodak 5219 (500T)": (9.0, 28.0, 55.0, 12.0),
    "35mm Fuji Eterna 500": (8.0, 26.0, 50.0, 6.0),
    "16mm Kodak 7219": (14.0, 40.0, 65.0, 15.0),
    "Super 8 (Tri-X P&B)": (22.0, 55.0, 75.0, 0.0),
    "Personalizado (sliders)": "custom",
}


def film_grain(image: torch.Tensor, amount, size, roughness, color, seed, animate=True,
               device=None, clamp=True) -> torch.Tensor:
    b, h, w, c = image.shape
    dev = device or image.device
    strength = (max(0.0, amount) / 100.0) * _GRAIN_MAX_AMOUNT
    if strength <= 0.0:
        return image
    color01 = min(max(color, 0.0), 100.0) / 100.0
    rough01 = min(max(roughness, 0.0), 100.0) / 100.0
    scale = 1.0 + (min(max(size, 0.0), 100.0) / 100.0) * _GRAIN_SIZE_SCALE_MAX
    fine_h, fine_w = max(1, round(h / scale)), max(1, round(w / scale))
    coarse_h = max(1, round(h / (scale * _GRAIN_COARSE_MULT)))
    coarse_w = max(1, round(w / (scale * _GRAIN_COARSE_MULT)))

    frames = b if animate else 1
    gen = torch.Generator(device=dev).manual_seed(int(seed) & 0xFFFFFFFFFFFFFFFF)
    fine = torch.randn(frames, 3, fine_h, fine_w, generator=gen, device=dev)
    coarse = torch.randn(frames, 3, coarse_h, coarse_w, generator=gen, device=dev)
    weights = torch.tensor(_GRAIN_CHANNEL_W, device=dev).view(1, 1, 1, 3)
    out = torch.empty_like(image)

    for s in range(0, b, _GRAIN_FRAME_CHUNK):
        e = min(b, s + _GRAIN_FRAME_CHUNK)
        idx = slice(s, e) if animate else slice(0, 1)
        n = e - s
        ff = F.interpolate(fine[idx], size=(h, w), mode="bilinear", align_corners=False)
        fc = F.interpolate(coarse[idx], size=(h, w), mode="bilinear", align_corners=False)
        field = ff * (1.0 - rough01 * _GRAIN_ROUGH_MAX) + fc * (rough01 * _GRAIN_ROUGH_MAX)
        field = field.permute(0, 2, 3, 1)
        if not animate and n > 1:
            field = field.expand(n, -1, -1, -1)
        std = field.reshape(field.shape[0], -1).std(dim=1).clamp_min(1e-5).view(-1, 1, 1, 1)
        field = field / std
        mono = field[..., 1:2].expand(-1, -1, -1, 3)
        grain = (mono * (1.0 - color01) + field * weights * color01) * strength
        chunk = image[s:e].to(dev).float()
        chunk[..., :3] = chunk[..., :3] + grain
        if clamp:
            chunk[..., :3] = chunk[..., :3].clamp(0.0, 1.0)
        out[s:e] = chunk.to(out.device, out.dtype)
    return out


# ===========================================================================
# Seed: procura no workflow (subindo pelos fios a partir deste node)
# ===========================================================================
_SEED_KEYS = ("seed", "noise_seed")
_VALUE_KEYS = ("seed", "noise_seed", "value", "int", "Value", "number")


def _is_link(v) -> bool:
    return isinstance(v, (list, tuple)) and len(v) == 2 and isinstance(v[1], int) and isinstance(v[0], (str, int))


def _resolve_int(prompt, v, depth=0):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and float(v).is_integer():
        return int(v)
    if _is_link(v) and depth < 6:
        node = prompt.get(str(v[0])) or {}
        ins = node.get("inputs", {}) or {}
        for k in _VALUE_KEYS:
            if k in ins:
                r = _resolve_int(prompt, ins[k], depth + 1)
                if r is not None:
                    return r
    return None


def find_seeds(prompt, unique_id):
    """Lista [{node, class, key, seed}] em ordem de proximidade (BFS a montante).
    Se nada estiver ligado a este node, cai pra busca no workflow inteiro."""
    if not isinstance(prompt, dict):
        return []
    found, seen = [], set()

    def scan(nid):
        node = prompt.get(str(nid)) or {}
        ins = node.get("inputs", {}) or {}
        for k in _SEED_KEYS:
            if k in ins:
                val = _resolve_int(prompt, ins[k])
                if val is not None:
                    found.append({"node": str(nid), "class": node.get("class_type", "?"), "key": k, "seed": val})
        return ins

    queue = [str(unique_id)] if unique_id is not None else []
    while queue:
        nid = queue.pop(0)
        if nid in seen:
            continue
        seen.add(nid)
        ins = scan(nid) if nid != str(unique_id) else (prompt.get(nid, {}) or {}).get("inputs", {}) or {}
        for v in ins.values():
            if _is_link(v):
                queue.append(str(v[0]))
    if not found:
        for nid in prompt:
            if str(nid) != str(unique_id):
                scan(nid)
    # sem duplicados (o mesmo seed via Primitive em varios samplers)
    uniq, keys = [], set()
    for f in found:
        k = (f["node"], f["key"])
        if k not in keys:
            keys.add(k)
            uniq.append(f)
    return uniq


# ===========================================================================
# Nome / versao
# ===========================================================================
def _resolve_tokens(prefix, ctx):
    def rep(m):
        k = m.group(1).lower()
        return str(ctx.get(k, m.group(0)))
    return re.sub(r"\{(\w+)\}", rep, prefix)


def _next_version(out_dir, stem):
    pat = re.compile(rf"^{re.escape(stem)}_v(\d{{3,}})(?:[._].*)?$", re.IGNORECASE)
    hi = 0
    try:
        for f in os.listdir(out_dir):
            m = pat.match(f)
            if m:
                hi = max(hi, int(m.group(1)))
    except FileNotFoundError:
        pass
    return hi + 1


def _safe_inside(base, path):
    base, path = os.path.abspath(base), os.path.abspath(path)
    try:
        return os.path.commonpath([base, path]) == base
    except ValueError:
        return False


# ===========================================================================
# Encoders
# ===========================================================================
def _to_u8(x: torch.Tensor) -> np.ndarray:
    return (x.clamp(0, 1) * 255.0).round().to(torch.uint8).cpu().numpy()


def _to_u16(x: torch.Tensor) -> np.ndarray:
    return (x.clamp(0, 1) * 65535.0).round().to(torch.int32).cpu().numpy().astype(np.uint16)


class VideoWriter:
    """Encoder incremental: add(frame) um por vez (nada de lote inteiro na RAM).
    Escreve num arquivo temporario e so publica no close(), depois de validar."""

    def __init__(self, path, fps, codec, pix_fmt, bits, options=None, metadata=None, display_709=True):
        if not _HAS_AV:
            raise RuntimeError(f"{TAG} PyAV ('av') nao encontrado -- ele vem com o ComfyUI.")
        self.path, self.fps, self.codec, self.pix_fmt, self.bits = path, fps, codec, pix_fmt, bits
        self.options, self.metadata = dict(options or {}), metadata
        # display_709: o conteudo e sRGB/Rec.709 de tela -> marca primarias/curva bt709.
        # Para masters log/linear (LogC4, ACEScct...) so a MATRIZ e marcada.
        self.display_709 = display_709
        stem, ext = os.path.splitext(path)
        self.tmp = f"{stem}.writing-{uuid.uuid4().hex[:6]}{ext}"
        self.container = None
        self.stream = None
        self.n = 0

    def _open(self, h, w):
        self.container = av.open(self.tmp, mode="w")
        for k, v in (self.metadata or {}).items():
            try:
                self.container.metadata[k] = str(v)
            except Exception:
                pass
        rate = Fraction(float(self.fps)).limit_denominator(1001 * 1000)
        st = self.container.add_stream(self.codec, rate=rate)
        if self.pix_fmt.startswith(("yuv420", "yuv422")):
            w, h = w - (w % 2), h - (h % 2)
        st.width, st.height, st.pix_fmt = w, h, self.pix_fmt
        if self.codec == "libx265":
            st.codec_tag = "hvc1"  # QuickTime/Resolve abrem hvc1
        # COR: converter RGB->YUV com a matriz BT.709 e MARCAR isso no arquivo. Sem isso o
        # libav usa BT.601 sem tag, e todo player trata HD sem tag como BT.709 -> vermelho
        # puxa pro laranja/verde e o verde escurece.
        prim_trc = "bt709" if self.display_709 else "undef"
        vui = f"colorprim={prim_trc}:transfer={prim_trc}:colormatrix=bt709"
        if self.codec == "libx264":
            extra = vui + ":range=tv"
            self.options["x264-params"] = (self.options["x264-params"] + ":" + extra) if self.options.get("x264-params") else extra
        elif self.codec == "libx265":
            extra = vui + ":range=limited"
            self.options["x265-params"] = (self.options["x265-params"] + ":" + extra) if self.options.get("x265-params") else extra
        cc = st.codec_context
        for attr, val in (("colorspace", 1), ("color_range", 1),
                          ("color_primaries", 1 if self.display_709 else 2), ("color_trc", 1 if self.display_709 else 2)):
            try:
                setattr(cc, attr, val)
            except Exception:
                pass
        if self.options:
            st.options = {k: str(v) for k, v in self.options.items()}
        self.stream, self.w, self.h = st, w, h

    def add(self, fr: torch.Tensor):
        if self.container is None:
            self._open(int(fr.shape[0]), int(fr.shape[1]))
        fr = fr[: self.h, : self.w, :3]
        arr = _to_u16(fr) if self.bits > 8 else _to_u8(fr)
        vf = av.VideoFrame.from_ndarray(np.ascontiguousarray(arr), format="rgb48le" if self.bits > 8 else "rgb24")
        try:
            vf = vf.reformat(format=self.pix_fmt, dst_colorspace="ITU709", dst_color_range="MPEG")
        except TypeError:  # PyAV antigo sem dst_color_range
            vf = vf.reformat(format=self.pix_fmt, dst_colorspace="ITU709")
        vf.pts = self.n
        self.n += 1
        for pkt in self.stream.encode(vf):
            self.container.mux(pkt)

    def close(self):
        if self.container is None:
            raise RuntimeError("nenhum frame escrito")
        for pkt in self.stream.encode():
            self.container.mux(pkt)
        self.container.close()
        self.container = None
        if not os.path.isfile(self.tmp) or os.path.getsize(self.tmp) < 256:
            raise RuntimeError("arquivo vazio")
        os.replace(self.tmp, self.path)
        return self.path

    def abort(self):
        try:
            if self.container is not None:
                self.container.close()
        except Exception:
            pass
        try:
            if os.path.exists(self.tmp):
                os.remove(self.tmp)
        except OSError:
            pass


class ThreadedWriter:
    """VideoWriter numa thread propria: o encode (x264/x265/ProRes) roda em paralelo
    com o calculo do proximo frame. Fila curta pra nao acumular frames na RAM."""

    def __init__(self, writer: "VideoWriter", depth: int = 3):
        import queue
        import threading
        self.w, self.err = writer, None
        self.q = queue.Queue(maxsize=depth)
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    def _run(self):
        while True:
            fr = self.q.get()
            if fr is None:
                return
            if self.err is None:
                try:
                    self.w.add(fr)
                except Exception as e:  # pragma: no cover
                    self.err = e

    def add(self, fr):
        if self.err:
            raise self.err
        self.q.put(fr)

    def close(self):
        self.q.put(None)
        self.t.join()
        if self.err:
            raise self.err
        return self.w.close()

    def abort(self):
        try:
            self.q.put_nowait(None)
        except Exception:
            pass
        self.t.join(timeout=5)
        self.w.abort()


def encode_video(frame_fn, n, path, fps, codec, pix_fmt, bits, options=None, metadata=None):
    """frame_fn(i) -> tensor [H,W,3] 0..1."""
    w = VideoWriter(path, fps, codec, pix_fmt, bits, options, metadata)
    try:
        for i in range(n):
            w.add(frame_fn(i))
        return w.close()
    except Exception:
        w.abort()
        raise


def _exr_compression(name):
    import OpenEXR
    return {
        "ZIP": OpenEXR.ZIP_COMPRESSION, "PIZ": OpenEXR.PIZ_COMPRESSION,
        "DWAA": OpenEXR.DWAA_COMPRESSION, "ZIPS": OpenEXR.ZIPS_COMPRESSION,
        "none": OpenEXR.NO_COMPRESSION,
    }.get(name, OpenEXR.ZIP_COMPRESSION)


def write_exr(path, arr: np.ndarray, half: bool, compression: str, attrs: dict, chroma=None):
    data = np.ascontiguousarray(arr[..., :3].astype(np.float16 if half else np.float32))
    try:
        import OpenEXR
        header = {"compression": _exr_compression(compression), "type": OpenEXR.scanlineimage}
        if chroma:
            header["chromaticities"] = tuple(float(v) for v in chroma)
        for k, v in (attrs or {}).items():
            header[k] = str(v)
        with OpenEXR.File(header, {"RGB": data}) as f:
            f.write(path)
        return
    except ImportError:
        pass
    os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
    import cv2
    flags = [cv2.IMWRITE_EXR_TYPE, cv2.IMWRITE_EXR_TYPE_HALF if half else cv2.IMWRITE_EXR_TYPE_FLOAT]
    if not cv2.imwrite(path, data[..., ::-1].astype(np.float32), flags):
        raise RuntimeError(f"{TAG} nao consegui escrever EXR (instale: pip install \"OpenEXR>=3.3\")")


def write_png16(path, arr: np.ndarray):
    import cv2
    u16 = (np.clip(arr[..., :3], 0, 1) * 65535.0).round().astype(np.uint16)
    if not cv2.imwrite(path, np.ascontiguousarray(u16[..., ::-1])):
        raise RuntimeError(f"{TAG} falha ao escrever {path}")


# ===========================================================================
# Burn-in (texto no frame)
# ===========================================================================
_FONT_CACHE = {}


def _font(px):
    from PIL import ImageFont
    if px in _FONT_CACHE:
        return _FONT_CACHE[px]
    f = None
    for name in ("consola.ttf", "DejaVuSansMono.ttf", "Menlo.ttc", "arial.ttf"):
        try:
            f = ImageFont.truetype(name, px)
            break
        except Exception:
            continue
    if f is None:
        try:
            f = ImageFont.load_default(size=px)
        except Exception:
            f = ImageFont.load_default()
    _FONT_CACHE[px] = f
    return f


def burn_text(frame: torch.Tensor, text: str) -> torch.Tensor:
    from PIL import Image, ImageDraw
    h, w = frame.shape[:2]
    px = max(10, int(h * 0.028))
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    while True:  # encolhe a fonte ate caber na largura
        font = _font(px)
        l, t, r, b = probe.textbbox((0, 0), text, font=font)
        if (r - l) + px * 2 <= w or px <= 8:
            break
        px = max(8, int(px * 0.85))
    pad = px // 2
    bw, bh = r - l + pad * 2, b - t + pad * 2
    bw, bh = min(bw, w), min(bh, h)
    # caixa preta 60% + texto branco, so na regiao (rapido)
    box = Image.new("RGBA", (bw, bh), (0, 0, 0, 153))
    ImageDraw.Draw(box).text((pad - l, pad - t), text, font=font, fill=(255, 255, 255, 255))
    rgba = torch.from_numpy(np.array(box)).to(frame.device).float() / 255.0
    y0, x0 = h - bh - pad, pad
    y0 = max(0, y0)
    region = frame[y0:y0 + bh, x0:x0 + bw, :3]
    a = rgba[: region.shape[0], : region.shape[1], 3:4]
    frame = frame.clone()
    frame[y0:y0 + bh, x0:x0 + bw, :3] = region * (1 - a) + rgba[: region.shape[0], : region.shape[1], :3] * a
    return frame


# ===========================================================================
# Preview (proxy all-intra + filmstrip)
# ===========================================================================
PROXY_MAX_SIDE = 1280
FILMSTRIP_THUMBS = 24
FILMSTRIP_H = 64


def _resize_frame(fr: torch.Tensor, max_side: int) -> torch.Tensor:
    h, w = fr.shape[:2]
    sc = min(1.0, max_side / float(max(h, w)))
    nw, nh = max(2, int(round(w * sc)) // 2 * 2), max(2, int(round(h * sc)) // 2 * 2)
    if (nw, nh) == (w, h):
        return fr
    x = fr[..., :3].permute(2, 0, 1).unsqueeze(0).float()
    x = F.interpolate(x, size=(nh, nw), mode="area" if sc < 1 else "bilinear")
    return x[0].permute(1, 2, 0)


def filmstrip_indices(n):
    k = min(FILMSTRIP_THUMBS, n)
    return [int(round(i * (n - 1) / max(1, k - 1))) for i in range(k)] if k > 1 else [0]


def filmstrip_thumb(fr: torch.Tensor):
    from PIL import Image
    h, w = fr.shape[:2]
    tw = max(2, int(round(w * FILMSTRIP_H / h)))
    t = _resize_frame(fr, max(tw, FILMSTRIP_H))
    return Image.fromarray(_to_u8(t)).resize((tw, FILMSTRIP_H))


def write_filmstrip(thumbs, path):
    from PIL import Image
    tw = thumbs[0].width
    sheet = Image.new("RGB", (tw * len(thumbs), FILMSTRIP_H))
    for j, im in enumerate(thumbs):
        sheet.paste(im, (j * tw, 0))
    sheet.save(path, quality=80)
    return len(thumbs), tw


def _view_ref(path):
    """{filename, subfolder, type} p/ a rota /view do ComfyUI."""
    if not _HAS_FP:
        return {"filename": os.path.basename(path), "subfolder": "", "type": "temp"}
    for kind, base in (("output", folder_paths.get_output_directory()),
                       ("temp", folder_paths.get_temp_directory())):
        if _safe_inside(base, path):
            sub = os.path.relpath(os.path.dirname(path), base)
            return {"filename": os.path.basename(path),
                    "subfolder": "" if sub == "." else sub.replace("\\", "/"), "type": kind}
    return {"filename": os.path.basename(path), "subfolder": "", "type": "temp"}


# ===========================================================================
# Normalizacao da entrada
# ===========================================================================
def _frames_view(t: torch.Tensor) -> torch.Tensor:
    """Visao [N,H,W,C] do tensor de entrada SEM copiar (so reshape/permute)."""
    while t.dim() > 4:
        t = t[0] if t.shape[0] == 1 else t.reshape(-1, *t.shape[-3:])
    if t.dim() == 2:
        t = t.unsqueeze(0)
    if t.dim() == 3:          # MASK [N,H,W]
        t = t.unsqueeze(-1)
    if t.shape[-1] not in (1, 3, 4) and t.shape[1] in (1, 3, 4):
        t = t.permute(0, 2, 3, 1)
    return t


_NAN_WARNED = {"v": False}


def _frame1(view: torch.Tensor, i: int, clamp: bool, dev) -> torch.Tensor:
    """Um frame [H,W,3] float32 no device, ja com canais/NaN/clamp resolvidos."""
    fr = view[i].to(dev).float()
    c = fr.shape[-1]
    if c == 1:
        fr = fr.expand(-1, -1, 3)
    elif c == 2:
        fr = fr[..., :1].expand(-1, -1, 3)
    elif c >= 4:
        fr = fr[..., :3]
    if not torch.isfinite(fr).all():
        if not _NAN_WARNED["v"]:
            print(f"{TAG} *** ATENCAO: NaN/Inf nos frames (viram preto). Causa tipica: fp16 estourando.", flush=True)
            _NAN_WARNED["v"] = True
        fr = fr.nan_to_num(0.0, posinf=1.0 if clamp else HDR_MAX, neginf=0.0)
    return fr.clamp(0.0, 1.0) if clamp else fr.contiguous()


def grain_field(h, w, amount, size, roughness, color, seed, idx, animate, dev) -> torch.Tensor:
    """Grao de UM frame [H,W,3] (mesma matematica do film_grain / NKD), semente por frame."""
    strength = (max(0.0, amount) / 100.0) * _GRAIN_MAX_AMOUNT
    if strength <= 0.0:
        return None
    color01 = min(max(color, 0.0), 100.0) / 100.0
    rough01 = min(max(roughness, 0.0), 100.0) / 100.0
    scale = 1.0 + (min(max(size, 0.0), 100.0) / 100.0) * _GRAIN_SIZE_SCALE_MAX
    fh, fw = max(1, round(h / scale)), max(1, round(w / scale))
    ch, cw = max(1, round(h / (scale * _GRAIN_COARSE_MULT))), max(1, round(w / (scale * _GRAIN_COARSE_MULT)))
    s = (int(seed) * 1000003 + (int(idx) if animate else 0)) & 0x7FFFFFFFFFFFFFFF
    gen = torch.Generator(device=dev).manual_seed(s)
    fine = torch.randn(1, 3, fh, fw, generator=gen, device=dev)
    coarse = torch.randn(1, 3, ch, cw, generator=gen, device=dev)
    ff = F.interpolate(fine, size=(h, w), mode="bilinear", align_corners=False)
    fc = F.interpolate(coarse, size=(h, w), mode="bilinear", align_corners=False)
    field = (ff * (1.0 - rough01 * _GRAIN_ROUGH_MAX) + fc * (rough01 * _GRAIN_ROUGH_MAX))[0].permute(1, 2, 0)
    field = field / field.std().clamp_min(1e-5)
    weights = torch.tensor(_GRAIN_CHANNEL_W, device=dev).view(1, 1, 3)
    mono = field[..., 1:2].expand(-1, -1, 3)
    return (mono * (1.0 - color01) + field * weights * color01) * strength


def _linked_slots(prompt, uid):
    """Quais saidas deste node estao ligadas em alguem (None = nao sei -> todas)."""
    if not isinstance(prompt, dict) or uid is None:
        return None
    used = set()
    for node in prompt.values():
        for v in ((node or {}).get("inputs", {}) or {}).values():
            if _is_link(v) and str(v[0]) == str(uid):
                used.add(int(v[1]))
    return used


def _normalize(t: torch.Tensor, clamp: bool = True) -> torch.Tensor:
    while t.dim() > 4:
        t = t[0] if t.shape[0] == 1 else t.reshape(-1, *t.shape[-3:])
    if t.dim() == 3:
        t = t.unsqueeze(-1)
    if t.dim() == 2:
        t = t.unsqueeze(0).unsqueeze(-1)
    if t.shape[-1] not in (1, 3, 4) and t.shape[1] in (1, 3, 4):
        t = t.permute(0, 2, 3, 1)
    c = t.shape[-1]
    if c == 1:
        t = t.repeat(1, 1, 1, 3)
    elif c == 2:
        t = t[..., :1].repeat(1, 1, 1, 3)
    elif c >= 4:
        t = t[..., :3]
    t = t.float()
    if torch.isnan(t).any() or torch.isinf(t).any():
        print(f"{TAG} *** ATENCAO: NaN/Inf nos frames (viram preto). Causa tipica: fp16 estourando.", flush=True)
        t = t.nan_to_num(0.0, posinf=1.0 if clamp else HDR_MAX, neginf=0.0)
    return (t.clamp(0.0, 1.0) if clamp else t).contiguous()


# ===========================================================================
# NODE
# ===========================================================================
class BruxosSaveVideoPro:
    @classmethod
    def INPUT_TYPES(cls):
        # ORDEM IMPORTA: widgets_values e posicional. Novos campos -> sempre no FIM.
        return {
            "required": {
                "images": ("IMAGE", {"tooltip": "Frames a salvar (sRGB do VAE)."}),
                "fps": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 240.0, "step": 0.001}),
                "format": (list(FORMATS.keys()), {"default": "mp4 / h264",
                    "tooltip": "Master. Sequencias (EXR/PNG) vao numa pasta propria."}),
                "crf": ("INT", {"default": 16, "min": 0, "max": 51,
                    "tooltip": "Qualidade h264/h265 (menor = melhor). 14-18 = visualmente sem perda."}),
                "preset": (["ultrafast", "veryfast", "fast", "medium", "slow", "slower", "veryslow"],
                    {"default": "medium", "tooltip": "Velocidade x eficiencia do x264/x265."}),
                "color_space": (COLOR_SPACES, {"default": COLOR_SPACES[0],
                    "tooltip": "Espaco de cor do MASTER. O player sempre mostra sRGB. "
                               "Linear (ACEScg/AP0/Rec709) -> use EXR. LogC4/ACEScct funcionam "
                               "em ProRes/h265 10-bit/PNG 16 tambem."}),
                "input_interpretation": (INPUT_INTERP, {"default": INPUT_INTERP[0],
                    "tooltip": "Como linearizar a saida do modelo (display-referred). "
                               "'ACES 2.0 SDR inverso' desfaz a view transform ACES pelo OCIO e "
                               f"recupera altas (teto {ACES_INV_HIGHLIGHT_CAP:g})."}),
                "exr_compression": (EXR_COMPRESSION, {"default": "ZIP",
                    "tooltip": "ZIP/PIZ sem perda; DWAA com perda e bem menor."}),
                "filename_prefix": ("STRING", {"default": "Bruxos/{date}/render",
                    "tooltip": "Caminho dentro de output. Tokens: {date} {time} {seed} {fps} "
                               "{width} {height} {format}."}),
                "start_frame": ("INT", {"default": 1001, "min": 0, "max": 10_000_000,
                    "tooltip": "Numero do primeiro frame (sequencias, burn-in e contador do player)."}),
                "seed_mode": (["auto (do workflow)", "manual", "off"], {"default": "auto (do workflow)",
                    "tooltip": "Auto: acha o seed do KSampler a montante. Manual: usa o campo seed "
                               "(da pra ligar um fio nele)."}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFFFFFFFFFF}),
                "seed_in_filename": ("BOOLEAN", {"default": False,
                    "tooltip": "Acrescenta _s<seed> no nome do arquivo."}),
                "burn_in": (["off", "seed", "seed + frame", "seed + frame + nome"], {"default": "off",
                    "tooltip": "Queima o texto no canto do MASTER (e no preview)."}),
                "grain_preset": (list(GRAIN_PRESETS.keys()), {"default": "off",
                    "tooltip": "Grao de filme (algoritmo do NKD Film Grain). 'Personalizado' usa os sliders."}),
                "grain_strength": ("FLOAT", {"default": 100.0, "min": 0.0, "max": 300.0, "step": 5.0,
                    "tooltip": "% de intensidade aplicada ao preset escolhido."}),
                "grain_amount": ("FLOAT", {"default": 3.0, "min": 0.0, "max": 100.0, "step": 0.5}),
                "grain_size": ("FLOAT", {"default": 25.0, "min": 0.0, "max": 100.0, "step": 0.5}),
                "grain_roughness": ("FLOAT", {"default": 50.0, "min": 0.0, "max": 100.0, "step": 0.5}),
                "grain_color": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 100.0, "step": 1.0}),
                "grain_animate": ("BOOLEAN", {"default": True, "tooltip": "Grao novo a cada frame."}),
                "grain_seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFFFFFFFFFF}),
                "pingpong": ("BOOLEAN", {"default": False}),
                "review_mp4": ("BOOLEAN", {"default": True,
                    "tooltip": "Com EXR/PNG: grava tambem um _review.mp4 sRGB do lado da pasta."}),
                "save_output": ("BOOLEAN", {"default": True,
                    "tooltip": "Desligado: so preview (vai pra temp, nada em output)."}),
                # --- HDR (append-only) ---
                "hdr_input": (HDR_INPUTS, {"default": HDR_INPUTS[0],
                    "tooltip": "LTX-2 HDR: ligue a saida do VAE Decode direto aqui e escolha ACEScct "
                               "(ou LogC3 nos LoRAs antigos) -- substitui o LTXVHDR Decode Postprocess. "
                               "Ou ligue o hdr_linear do node do LTX e escolha o linear correspondente. "
                               "A luz de cena vai SEM CORTE pro master (EXR, LogC4, LogC3, ACEScct...)."}),
                "hdr_view": (HDR_VIEWS, {"default": HDR_VIEWS[0],
                    "tooltip": "Como mostrar o HDR em SDR (player, review mp4, saida images e master sRGB). "
                               "Nao muda o master scene-linear/log."}),
                "hdr_exposure": ("FLOAT", {"default": 0.0, "min": -10.0, "max": 10.0, "step": 0.1,
                    "tooltip": "Exposicao em stops so da visualizacao (como o 'exposure' do LTX)."}),
            },
            "optional": {
                "audio": ("AUDIO",),
                "bruto": ("IMAGE,MASK,VIDEO", {"tooltip": "Opcional: o plate BRUTO / referencia (IMAGE, MASK ou VIDEO) "
                                    "para comparar no player: cortina, lado a lado, diferenca e "
                                    "alternar. Alinhado frame a frame; redimensionado ao tamanho do render. "
                                    "Nao vai pro arquivo salvo."}),
            },
            "hidden": {"prompt": "PROMPT", "unique_id": "UNIQUE_ID", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    RETURN_TYPES = ("IMAGE", "STRING", "INT", "IMAGE")
    RETURN_NAMES = ("images", "filepath", "seed", "scene_linear")
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "Bruxos do VFX/Video"

    # ------------------------------------------------------------------
    @staticmethod
    def _bruto_proxy(bruto, pingpong, n, H, W, fps, tmp_root, key):
        """Proxy all-intra do plate bruto, com o MESMO tamanho e numero de frames
        do proxy do render -> o player sincroniza os dois pelo indice do frame.
        Bruto mais curto: segura o ultimo frame. Mais longo: corta."""
        if not isinstance(bruto, torch.Tensor):
            # VIDEO nativo do ComfyUI
            try:
                bruto = bruto.get_components().images
            except Exception as e:
                raise RuntimeError(f"tipo de referencia nao suportado: {type(bruto).__name__} ({e})")
        bv = _frames_view(bruto)
        nb0 = int(bv.shape[0])
        order = list(range(nb0)) + (list(range(nb0 - 2, 0, -1)) if pingpong and nb0 > 2 else [])
        nb = len(order)
        bh, bw = int(bv.shape[1]), int(bv.shape[2])
        if abs(bw / bh - W / H) > 0.01:
            print(f"{TAG} bruto: proporcao {bw}x{bh} difere do render {W}x{H}; esticando p/ alinhar.", flush=True)
        if nb != n:
            print(f"{TAG} bruto: {nb} frames vs render {n}; "
                  f"{'segurando o ultimo' if nb < n else 'cortando o excesso'}.", flush=True)
        dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        def frame(i):
            fr = _frame1(bv, order[min(i, nb - 1)], True, dev)
            if (bh, bw) != (H, W):
                x = fr.permute(2, 0, 1).unsqueeze(0)
                fr = F.interpolate(x, size=(H, W), mode="bilinear", align_corners=False)[0].permute(1, 2, 0)
            return _resize_frame(fr, PROXY_MAX_SIDE)

        path = os.path.join(tmp_root, f"pro_{key}_bruto.mp4")
        encode_video(frame, n, path, fps, "libx264", "yuv420p", 8,
                     {"crf": 20, "preset": "veryfast", "x264-params": "keyint=1:min-keyint=1:scenecut=0"})
        return _view_ref(path), nb

    def save(self, images, fps, format, crf, preset, color_space, input_interpretation,
             exr_compression, filename_prefix, start_frame, seed_mode, seed, seed_in_filename,
             burn_in, grain_preset, grain_strength, grain_amount, grain_size, grain_roughness,
             grain_color, grain_animate, grain_seed, pingpong, review_mp4, save_output,
             hdr_input=HDR_INPUTS[0], hdr_view=HDR_VIEWS[0], hdr_exposure=0.0,
             audio=None, bruto=None, prompt=None, unique_id=None, extra_pnginfo=None):
        fmt = FORMATS.get(format, FORMATS["mp4 / h264"])
        dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        is_hdr = not str(hdr_input).startswith("off")
        _NAN_WARNED["v"] = False

        # Tudo e feito FRAME A FRAME: nenhuma copia do lote inteiro (4K x 129 frames
        # em float32 = 13 GB por copia). Um laco so alimenta master, review, proxy,
        # filmstrip e as saidas que estiverem ligadas.
        view = _frames_view(images)
        n0 = int(view.shape[0])
        if n0 == 0:
            raise RuntimeError(f"{TAG} nenhum frame recebido")
        order = list(range(n0)) + (list(range(n0 - 2, 0, -1)) if pingpong and n0 > 2 else [])
        n, H, W = len(order), int(view.shape[1]), int(view.shape[2])

        # ---- seed ----------------------------------------------------
        seeds = []
        if seed_mode.startswith("auto"):
            seeds = find_seeds(prompt, unique_id)
            the_seed = seeds[0]["seed"] if seeds else None
            if the_seed is None:
                print(f"{TAG} seed: nenhum KSampler encontrado no workflow -- usando o campo seed.", flush=True)
                the_seed = int(seed)
        elif seed_mode == "manual":
            the_seed = int(seed)
        else:
            the_seed = None

        # ---- grain ---------------------------------------------------
        g = GRAIN_PRESETS.get(grain_preset)
        grain_info, gp = None, None
        if g == "custom":
            g = (grain_amount, grain_size, grain_roughness, grain_color)
            k = 1.0
        else:
            k = grain_strength / 100.0
        if g:
            amt, sz, rough, col = g
            amt *= k
            gseed = int(grain_seed) if grain_seed else int(the_seed or 0)
            gp = (amt, sz, rough, col, gseed, bool(grain_animate))
            grain_info = {"preset": grain_preset, "amount": round(amt, 3), "size": sz,
                          "roughness": rough, "color": col, "animate": bool(grain_animate), "seed": gseed}

        def graded(i):
            """display 0..1 (normal) ou ACEScg scene-linear (HDR), ja com grao."""
            x = _frame1(view, order[i], not is_hdr, dev)
            gr = grain_field(H, W, gp[0], gp[1], gp[2], gp[3], gp[4], i, gp[5], dev) if gp else None
            if is_hdr:
                sc = hdr_to_ap1(x, hdr_input)
                if gr is not None:  # grao no dominio log (ACEScct): mesma textura na sombra e na alta
                    sc = _acescct_inv(_acescct(sc) + gr).clamp(-1.0, HDR_MAX)
                return sc
            return (x + gr).clamp(0.0, 1.0) if gr is not None else x

        def to_display(gr):
            if is_hdr:
                return render_hdr_view(gr.unsqueeze(0), hdr_view, hdr_exposure, hdr_input)[0].to(dev)
            return gr

        # ---- nome / pastas --------------------------------------------
        now = datetime.datetime.now()
        ctx = {"date": now.strftime("%Y-%m-%d"), "time": now.strftime("%H%M%S"),
               "seed": the_seed if the_seed is not None else "noseed",
               "fps": f"{fps:g}", "width": W, "height": H, "format": fmt["ext"]}
        prefix = _resolve_tokens(str(filename_prefix or "Bruxos/render"), ctx).replace("\\", "/").strip("/")
        if _HAS_FP:
            root = folder_paths.get_output_directory() if save_output else os.path.join(
                folder_paths.get_temp_directory(), "bruxos_pro")
            tmp_root = os.path.join(folder_paths.get_temp_directory(), "bruxos_pro")
        else:
            root = os.path.abspath("output" if save_output else "temp/bruxos_pro")
            tmp_root = os.path.abspath("temp/bruxos_pro")
        out_dir = os.path.abspath(os.path.join(root, os.path.dirname(prefix)))
        if not _safe_inside(root, out_dir):
            raise RuntimeError(f"{TAG} filename_prefix sai da pasta de output: {filename_prefix!r}")
        os.makedirs(out_dir, exist_ok=True)
        os.makedirs(tmp_root, exist_ok=True)
        stem = os.path.basename(prefix) or "render"
        if "#" in stem:
            print(f"{TAG} aviso: '#' no nome ({stem}) -- Nuke/Resolve leem '#' como numero de frame; trocando por '_'.", flush=True)
            stem = stem.replace("#", "_")
        if seed_in_filename and the_seed is not None:
            stem = f"{stem}_s{the_seed}"
        version = _next_version(out_dir, stem)
        base = f"{stem}_v{version:03d}"

        def label(i):
            if burn_in == "off":
                return None
            parts = [f"seed {the_seed if the_seed is not None else '-'}"]
            if "frame" in burn_in:
                parts.append(f"frame {start_frame + i}")
            if "nome" in burn_in:
                parts.append(base)
            return "  |  ".join(parts)

        if fmt["kind"] != "exr" and color_space in _LINEAR_SPACES:
            print(f"{TAG} aviso: '{color_space}' e linear e vai ser quantizado em inteiro "
                  f"({format}) -- sombras vao bandar. Para linear, use EXR.", flush=True)

        meta = {
            "seed": the_seed, "seeds_found": seeds, "frames": n, "fps": fps,
            "width": W, "height": H, "start_frame": start_frame, "format": format,
            "color_space": color_space, "ocio_colorspace": _OCIO_NAME.get(color_space),
            "input_interpretation": (input_interpretation if not color_space.startswith("sRGB") and not is_hdr else None),
            "hdr_input": hdr_input if is_hdr else None,
            "hdr_view": hdr_view if is_hdr else None, "hdr_exposure": float(hdr_exposure) if is_hdr else None,
            "grain": grain_info, "burn_in": burn_in, "version": version,
            "created": now.isoformat(timespec="seconds"),
        }
        container_meta = {"comment": json.dumps({k: meta[k] for k in ("seed", "color_space", "grain")},
                                                ensure_ascii=False),
                          "title": base}

        # ---- saidas que alguem usa ------------------------------------
        linked = _linked_slots(prompt, unique_id)
        want_images = linked is None or 0 in linked
        want_scene = linked is None or 3 in linked
        passthrough = (not is_hdr and gp is None and len(order) == n0 and view.shape[-1] == 3
                       and isinstance(images, torch.Tensor) and images.dim() == 4)
        out_img = (torch.empty((n, H, W, 3), dtype=torch.float32)
                   if want_images and not passthrough else None)
        out_scene = torch.empty((n, H, W, 3), dtype=torch.float32) if want_scene else None

        # ---- writers ---------------------------------------------------
        key = hashlib.md5(f"{base}|{uuid.uuid4().hex}".encode()).hexdigest()[:12]
        proxy = os.path.join(tmp_root, f"pro_{key}.mp4")
        writers = []
        master_path = review_path = ""
        mw = rw = None
        seq_dir = None
        if fmt["kind"] == "video":
            master_path = os.path.join(out_dir, f"{base}.{fmt['ext']}")
            opts = {}
            if fmt["codec"] in ("libx264", "libx265"):
                opts = {"crf": int(crf), "preset": preset}
                if fmt["codec"] == "libx265":
                    opts["x265-params"] = "log-level=error"
            if fmt.get("profile"):
                opts["profile"] = fmt["profile"]
                opts["vendor"] = "apl0"
            mw = ThreadedWriter(VideoWriter(master_path, fps, fmt["codec"], fmt["pix_fmt"], fmt["bits"], opts, container_meta,
                                            display_709=color_space.startswith("sRGB")))
            writers.append(mw)
        else:
            seq_dir = os.path.join(out_dir, base)
            os.makedirs(seq_dir, exist_ok=True)
            pad = max(4, len(str(start_frame + n - 1)))
            master_path = os.path.join(seq_dir, f"{base}.{'#' * pad}.{fmt['ext']}")
            attrs = {"bruxos:seed": the_seed if the_seed is not None else "",
                     "oiio:ColorSpace": _OCIO_NAME.get(color_space, ""),
                     "colorSpace": _EXR_TAG.get(color_space, ""),
                     "bruxos:frames": n, "bruxos:fps": f"{fps:g}"}
            chroma = _CHROMA.get(color_space)
            if review_mp4:
                review_path = os.path.join(out_dir, f"{base}_review.mp4")
                rw = ThreadedWriter(VideoWriter(review_path, fps, "libx264", "yuv420p", 8,
                                                {"crf": 18, "preset": "fast"}, container_meta))
                writers.append(rw)
        pw = ThreadedWriter(VideoWriter(proxy, fps, "libx264", "yuv420p", 8,
                                        {"crf": 20, "preset": "veryfast", "x264-params": "keyint=1:min-keyint=1:scenecut=0"}))
        writers.append(pw)

        def write_seq(i, arr):
            p_ = os.path.join(seq_dir, f"{base}.{start_frame + i:0{pad}d}.{fmt['ext']}")
            if fmt["kind"] == "exr":
                write_exr(p_, arr, fmt["half"], exr_compression, attrs, chroma)
            else:
                write_png16(p_, arr)

        thumb_at = set(filmstrip_indices(n))
        thumbs = []
        pool = ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 4)) if seq_dir else None
        pending = []
        try:
            for i in range(n):
                gr = graded(i)
                disp_clean = to_display(gr)
                lab = label(i)
                disp = burn_text(disp_clean, lab) if lab else disp_clean
                if is_hdr:
                    master = disp if color_space.startswith("sRGB") else ap1_to_target(gr, color_space)
                else:
                    master = display_to_target(disp, color_space, input_interpretation)
                if mw is not None:
                    mw.add(master)
                else:
                    pending.append(pool.submit(write_seq, i, master.float().cpu().numpy()))
                    if len(pending) >= 6:  # limita frames em voo (RAM)
                        pending.pop(0).result()
                if rw is not None:
                    rw.add(disp)
                pw.add(_resize_frame(disp, PROXY_MAX_SIDE))
                if i in thumb_at:
                    thumbs.append(filmstrip_thumb(disp_clean))
                if out_img is not None:
                    out_img[i] = disp_clean.cpu()
                if out_scene is not None:
                    out_scene[i] = (gr.clamp_min(0.0) if is_hdr else
                                    display_to_target(disp_clean, "ACEScg (AP1 linear)", input_interpretation)).cpu()
            for f in pending:
                f.result()
            for w in writers:
                w.close()
        except Exception:
            for w in writers:
                w.abort()
            raise
        finally:
            if pool:
                pool.shutdown(wait=True)

        if audio is not None and _video is not None:
            for pth in (master_path if mw is not None else None, review_path or None, proxy):
                if pth:
                    _video._mux_audio(pth, audio, fps)

        # ---- sidecar json --------------------------------------------
        meta["path"] = master_path
        meta["review"] = review_path or None
        sidecar = os.path.join(out_dir, f"{base}.json")
        try:
            with open(sidecar, "w", encoding="utf-8") as fh:
                json.dump({**meta, "prompt": prompt}, fh, ensure_ascii=False, indent=1, default=str)
        except Exception as e:
            logging.warning(f"{TAG} sidecar json falhou: {e}")

        bruto_ref, bruto_frames = None, 0
        if bruto is not None:
            try:
                bruto_ref, bruto_frames = self._bruto_proxy(bruto, pingpong, n, H, W, fps, tmp_root, key)
            except Exception as e:
                print(f"{TAG} bruto: nao consegui gerar o comparativo ({e})", flush=True)
        strip = os.path.join(tmp_root, f"pro_{key}_strip.jpg")
        nthumbs, _ = write_filmstrip(thumbs, strip)

        ui = {
            "preview": _view_ref(proxy), "filmstrip": _view_ref(strip), "thumbs": nthumbs,
            "fps": float(fps), "frames": n, "start_frame": int(start_frame),
            "width": W, "height": H, "seed": the_seed, "seeds": seeds,
            "path": master_path, "review": review_path, "sidecar": sidecar,
            "format": format, "color_space": color_space,
            "grain": grain_info["preset"] if grain_info else None,
            "saved": bool(save_output), "version": version,
            "has_audio": audio is not None,
            "bruto": bruto_ref, "bruto_frames": bruto_frames,
            "hdr": (hdr_input.split("·")[-1].split("(")[0].strip() + " → " + hdr_view.split("(")[0].strip()) if is_hdr else None,
        }
        print(f"{TAG} {master_path}  ({n} frames, {W}x{H}, seed={the_seed}, {color_space})", flush=True)
        tiny = torch.zeros((1, 64, 64, 3), dtype=torch.float32)
        if passthrough and want_images:
            out_images = images
        else:
            out_images = out_img if out_img is not None else tiny
        return {"ui": {"bruxos_pro": [ui]},
                "result": (out_images, master_path, int(the_seed or 0),
                           out_scene if out_scene is not None else tiny)}

BruxosSaveVideoPro.DESCRIPTION = (
    "Save Video Pro (Bruxos) -- saida final num node so: player frame a frame "
    "(setas, J/K/L, contador de frame e timecode), seed da geracao detectada no "
    "workflow (nome, metadados, sidecar .json e burn-in opcional), film grain com "
    "presets de negativo, e masters em h264/h265 10-bit/ProRes/EXR/PNG16 com "
    "ACEScg, ACES2065-1, ACEScct, ARRI LogC4 (AWG4) ou LogC3 (AWG3). Entrada HDR do LTX-2 "
    "(ACEScct/LogC3 do VAE Decode ou hdr_linear): substitui o LTXVHDR Decode Postprocess, "
    "sem cortar as altas, com view ACES 2.0 / Reinhard no player e saida scene_linear."
)

NODE_CLASS_MAPPINGS = {"BruxosSaveVideoPro": BruxosSaveVideoPro}
NODE_DISPLAY_NAME_MAPPINGS = {"BruxosSaveVideoPro": "🧙 Save Video Pro (Bruxos)"}


# ===========================================================================
# Rotas: abrir pasta no Explorer + presets de grain p/ o JS
# ===========================================================================
try:
    from server import PromptServer
    from aiohttp import web

    @PromptServer.instance.routes.post("/bruxos/pro/open_folder")
    async def _bruxos_pro_open(request):  # pragma: no cover
        data = await request.json()
        path = str(data.get("path", ""))
        base_ok = False
        for b in (folder_paths.get_output_directory(), folder_paths.get_temp_directory()):
            if _safe_inside(b, path):
                base_ok = True
        if not base_ok:
            return web.json_response({"error": "fora de output/temp"}, status=400)
        target = path if os.path.exists(path) else os.path.dirname(path)
        if "#" in os.path.basename(path):
            target = os.path.dirname(path)
        try:
            if sys.platform.startswith("win"):
                if os.path.isfile(target):
                    subprocess.Popen(["explorer", "/select,", os.path.normpath(target)])
                else:
                    os.startfile(os.path.normpath(target))  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", target] if os.path.isfile(target) else ["open", target])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(target) if os.path.isfile(target) else target])
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
        return web.json_response({"ok": True})

    @PromptServer.instance.routes.get("/bruxos/pro/grain_presets")
    async def _bruxos_pro_presets(request):  # pragma: no cover
        return web.json_response({k: v for k, v in GRAIN_PRESETS.items() if isinstance(v, tuple)})
except Exception:  # pragma: no cover
    pass
