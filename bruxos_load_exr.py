# -*- coding: utf-8 -*-
r"""
Bruxos do VFX — Load EXR (+ OCIO) com crop-box / fit / giro / flip
===================================================================
Mesmo contrato geometrico do Load Image Bruxos (fit_mode, target_*, aspect,
crop_x/y/w/h, girar, flip_*), mas para EXR float e com color management
OpenColorIO opcional, no espirito do ComfyUI-OCIO (Slava Sexton).

ORDEM DO PIPELINE (e por que):
  1. le o EXR em float32 (OpenEXR; cv2 so como fallback), layer escolhida,
     dataWindow encaixado no displayWindow (como o Read do Nuke)
  2. unpremultiply opcional (EXR costuma vir premultiplicado)
  3. exposure em stops -- em scene-linear, ANTES do OCIO
  4. girar / flip
  5. crop / fit / resize -- ainda em scene-linear (filtrar em luz linear e o
     correto), SEM clamp: valores > 1 e negativos sobrevivem
  6. OCIO (colorspace -> colorspace, ou display/view com tone map)
  7. clamp 0..1 opcional

Nada aqui clampa por padrao. O _bx_resize_img do Load Image clampa em 0..1,
por isso este arquivo tem o proprio resize (area pra reduzir, bilinear pra
ampliar: bicubico/lanczos criam aneis e negativos em bordas HDR).

Dependencias: OpenEXR>=3.3 (leitura), opencolorio (so se usar o CM),
opencv (resize; cai pra torch se faltar). Os tres ja vem com o ComfyUI-OCIO.
"""

from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
import threading
from collections import OrderedDict

import numpy as np

try:
    import torch
    import torch.nn.functional as F
except Exception:  # pragma: no cover
    torch = None
    F = None

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

try:
    import PyOpenColorIO as OCIO
    _HAS_OCIO = True
except Exception:  # pragma: no cover
    OCIO = None
    _HAS_OCIO = False

try:
    import folder_paths
except Exception:  # pragma: no cover
    folder_paths = None

try:
    from comfy_api.latest import io as _io
except Exception:  # pragma: no cover
    _io = None

try:
    from .bruxos_load_media import ASPECTS, FIT_MODES, _target_from_one
except Exception:  # pragma: no cover - import solto (testes)
    try:
        from bruxos_load_media import ASPECTS, FIT_MODES, _target_from_one
    except Exception:
        ASPECTS = ["livre", "1:1", "3:4", "4:3", "16:9", "9:16", "2:3", "3:2"]
        FIT_MODES = ["off (original)", "crop", "stretch", "pad (letterbox)"]

        def _target_from_one(W, H, tw, th):
            if tw > 0 and th > 0:
                return tw, th
            if tw > 0:
                return tw, max(1, round(H * tw / W))
            if th > 0:
                return max(1, round(W * th / H)), th
            return W, H


CAT = "Bruxos do VFX/Loaders"
NODE_ID = "BruxosLoadEXR"
TAG = "[Bruxos Load EXR]"

ROTATIONS = ["off", "90 (horario)", "-90 (anti-horario)", "180"]
EXR_WINDOWS = ["display window (Nuke)", "data window (tudo)"]

CM_OFF = "off (raw / linear)"
CM_CS = "colorspace (in -> out)"
CM_DV = "display / view (tone map)"
CM_MODES = [CM_OFF, CM_CS, CM_DV]

BUILTIN = "(built-in ACES studio config)"
ENV_CFG = "($OCIO do ambiente)"


# ===========================================================================
# arquivos
# ===========================================================================
def _input_dir():
    if folder_paths is not None:
        try:
            return folder_paths.get_input_directory()
        except Exception:
            pass
    return os.getcwd()


def _scan_input(exts):
    root = _input_dir()
    out = []
    try:
        for base, _dirs, files in os.walk(root):
            for name in files:
                if name.lower().endswith(exts):
                    out.append(os.path.relpath(os.path.join(base, name), root).replace("\\", "/"))
    except Exception:
        pass
    return sorted(out)


_FRAME_RE = re.compile(r"^(.*?)(\d+)(\.exr)$", re.I)
_PATTERN_RE = re.compile(r"^(.*?)(#+|%0(\d+)d|@+)(\.exr)$", re.I)


def _list_exr():
    """Frames de uma sequencia viram UMA entrada 'nome.####.exr' (senao o combo
    teria milhares de linhas). Arquivo solto continua com o nome real."""
    groups, singles = {}, []
    for rel in _scan_input((".exr",)):
        d, b = os.path.split(rel)
        m = _FRAME_RE.match(b)
        if m:
            groups.setdefault((d, m.group(1), len(m.group(2)), m.group(3)), []).append(rel)
        else:
            singles.append(rel)
    out = list(singles)
    for (d, pre, pad, ext), files in groups.items():
        if len(files) > 1:
            out.append((d + "/" if d else "") + pre + "#" * pad + ext)
        else:
            out.extend(files)
    return sorted(out)


def _scan_sequence(folder, prefix, pad, ext):
    """-> {frame: caminho} dos arquivos prefix + <pad digitos> + ext na pasta."""
    rx = re.compile(re.escape(prefix) + r"(\d{%d})" % pad + re.escape(ext) + "$", re.I)
    frames = {}
    try:
        for name in os.listdir(folder or "."):
            m = rx.match(name)
            if m:
                frames[int(m.group(1))] = os.path.join(folder, name)
    except FileNotFoundError:
        pass
    return frames


def _sequence_of(path):
    """Caminho de um frame OU padrao (####, %04d, @@@@) -> (frames dict, rotulo).
    Retorna ({}, None) se nao for sequencia."""
    folder, base = os.path.split(path)
    m = _PATTERN_RE.match(base)
    if m:
        pre, tok, pct, ext = m.group(1), m.group(2), m.group(3), m.group(4)
        pad = int(pct) if pct else len(tok)
        return _scan_sequence(folder, pre, pad, ext), pre + "#" * pad + ext
    m = _FRAME_RE.match(base)
    if m and os.path.isfile(path):
        pre, digits, ext = m.group(1), m.group(2), m.group(3)
        return _scan_sequence(folder, pre, len(digits), ext), pre + "#" * len(digits) + ext
    return {}, None


def _resolve_source(file, exr_path=""):
    """-> (caminho_unico, frames dict, rotulo). Aceita frame, padrao ou arquivo solto,
    tanto no exr_path (prioridade) quanto no seletor."""
    cands = []
    p = str(exr_path or "").strip().strip('"')
    if p:
        cands += [p, os.path.join(_input_dir(), p)]
    if file and not str(file).startswith("("):
        cands.append(os.path.join(_input_dir(), str(file)))
    for c in cands:
        c = os.path.abspath(c)
        frames, label = _sequence_of(c)
        if frames:
            single = c if os.path.isfile(c) else frames[min(frames)]
            return single, frames, label
        if os.path.isfile(c):
            return c, {}, None
    raise FileNotFoundError(f"{TAG} EXR nao encontrado: {file!r} / {exr_path!r}")


def _resolve_exr(file, exr_path=""):
    try:
        return _resolve_source(file, exr_path)[0]
    except FileNotFoundError:
        pass
    p = str(exr_path or "").strip().strip('"')
    if p:
        if os.path.isfile(p):
            return os.path.abspath(p)
        cand = os.path.join(_input_dir(), p)
        if os.path.isfile(cand):
            return os.path.abspath(cand)
    cand = os.path.join(_input_dir(), str(file or ""))
    if os.path.isfile(cand):
        return os.path.abspath(cand)
    raise FileNotFoundError(f"{TAG} EXR nao encontrado: {file!r} / {exr_path!r}")


# ===========================================================================
# leitura EXR
# ===========================================================================
class _LRU:
    def __init__(self, n):
        self.n, self.d, self.lock = n, OrderedDict(), threading.RLock()

    def get(self, k):
        with self.lock:
            if k in self.d:
                self.d.move_to_end(k)
                return self.d[k]
            return None

    def put(self, k, v):
        with self.lock:
            self.d[k] = v
            self.d.move_to_end(k)
            while len(self.d) > self.n:
                self.d.popitem(last=False)


_READ_CACHE = _LRU(4)

_SUFFIX = {
    "r": "R", "red": "R", "g": "G", "green": "G", "b": "B", "blue": "B",
    "a": "A", "alpha": "A", "y": "Y", "z": "Z",
}


def _split_name(name):
    """'diffuse.R' -> ('diffuse', 'R');  'R' -> ('', 'R'). Sufixo normalizado."""
    if "." in name:
        layer, suf = name.rsplit(".", 1)
    else:
        layer, suf = "", name
    return layer, _SUFFIX.get(suf.lower(), suf)


def _win(header, key):
    w = header.get(key)
    if w is None:
        return None
    (x0, y0), (x1, y1) = w
    return int(x0), int(y0), int(x1), int(y1)


def _read_exr_openexr(path):
    """-> dict layer -> {sufixo: array HxW float32}, data_window, display_window, extras."""
    import OpenEXR

    layers, dw, disp, extras = {}, None, None, {}
    with OpenEXR.File(path, separate_channels=True) as f:
        for part_i, part in enumerate(f.parts):
            hdr = part.header
            pdw = _win(hdr, "dataWindow")
            if dw is None:
                dw, disp = pdw, _win(hdr, "displayWindow")
                for k in ("chromaticities", "pixelAspectRatio", "compression", "framesPerSecond"):
                    if k in hdr:
                        extras[k] = str(hdr[k])
            pname = ""
            try:
                pname = part.name() or ""
            except Exception:
                pass
            for name, ch in part.channels.items():
                layer, suf = _split_name(name)
                # multi-part: canais "R/G/B" de uma parte nomeada viram layer com o nome da parte
                if not layer and pname and part_i > 0:
                    layer = pname
                if pdw != dw:
                    continue  # partes com outra janela ficam de fora (raro; evita desalinhamento)
                layers.setdefault(layer, {})[suf] = np.asarray(ch.pixels, dtype=np.float32)
    return layers, dw, disp, extras


def _read_exr_cv2(path):
    if cv2 is None:
        raise RuntimeError("sem OpenEXR e sem cv2")
    a = cv2.imread(path, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
    if a is None:
        raise RuntimeError("cv2 nao decodificou (OPENCV_IO_ENABLE_OPENEXR?)")
    a = a.astype(np.float32)
    if a.ndim == 2:
        chans = {"Y": a}
    else:
        chans = {"B": a[..., 0], "G": a[..., 1], "R": a[..., 2]}
        if a.shape[2] > 3:
            chans["A"] = a[..., 3]
    h, w = a.shape[:2]
    return {"": chans}, (0, 0, w - 1, h - 1), (0, 0, w - 1, h - 1), {"reader": "cv2 (fallback)"}


def _pick_layer(layers, wanted):
    wanted = str(wanted or "").strip()
    if wanted:
        if wanted in layers:
            return wanted
        low = {k.lower(): k for k in layers}
        if wanted.lower() in low:
            return low[wanted.lower()]
        raise RuntimeError(f"{TAG} layer {wanted!r} nao existe. Layers: {_layer_list(layers)}")
    if "" in layers and ({"R", "G", "B"} & set(layers[""]) or "Y" in layers[""]):
        return ""
    for k in ("rgba", "rgb", "beauty", "Combined", "combined"):
        if k in layers:
            return k
    return sorted(layers)[0]


def _layer_list(layers):
    order = {"R": 0, "G": 1, "B": 2, "A": 3}
    return [(k if k else "(rgba)") + ":" + "".join(sorted(v, key=lambda c: (order.get(c, 9), c)))
            for k, v in sorted(layers.items())]


def _assemble(chans):
    """dict sufixo->HxW -> (rgb HxWx3, alpha HxW|None)."""
    if {"R", "G", "B"} <= set(chans):
        rgb = np.stack([chans["R"], chans["G"], chans["B"]], -1)
    elif "Y" in chans:
        rgb = np.repeat(chans["Y"][..., None], 3, -1)
    else:
        # layer de 1-2 canais (Z, AO, uma so cor...): o primeiro vira cinza
        first = chans[sorted(k for k in chans if k != "A")[0]] if any(k != "A" for k in chans) else chans["A"]
        rgb = np.repeat(first[..., None], 3, -1)
    alpha = chans.get("A")
    return np.ascontiguousarray(rgb, np.float32), alpha


def _place_window(arr, dw, disp, fill):
    """Encaixa pixels do dataWindow no displayWindow (overscan cortado, buraco preenchido)."""
    if dw is None or disp is None or dw == disp:
        return arr
    dx0, dy0, _dx1, _dy1 = dw
    px0, py0, px1, py1 = disp
    W, H = px1 - px0 + 1, py1 - py0 + 1
    shape = (H, W) + arr.shape[2:]
    out = np.full(shape, fill, dtype=np.float32)
    h, w = arr.shape[:2]
    ox, oy = dx0 - px0, dy0 - py0
    sx0, sy0 = max(0, -ox), max(0, -oy)
    tx0, ty0 = max(0, ox), max(0, oy)
    cw, ch = min(w - sx0, W - tx0), min(h - sy0, H - ty0)
    if cw > 0 and ch > 0:
        out[ty0:ty0 + ch, tx0:tx0 + cw] = arr[sy0:sy0 + ch, sx0:sx0 + cw]
    return out


def read_exr(path, layer="", window=EXR_WINDOWS[0], use_cache=True):
    """-> (rgb float32 HxWx3, alpha float32 HxW | None, meta dict). Cacheado por mtime/size."""
    st = os.stat(path)
    key = (path, st.st_mtime_ns, st.st_size, str(layer), str(window))
    hit = _READ_CACHE.get(key) if use_cache else None
    if hit is not None:
        rgb, alpha, meta = hit
        return rgb.copy(), (None if alpha is None else alpha.copy()), dict(meta)

    try:
        layers, dw, disp, extras = _read_exr_openexr(path)
        extras.setdefault("reader", "OpenEXR")
    except ImportError:
        layers, dw, disp, extras = _read_exr_cv2(path)
    except Exception as e:
        try:
            layers, dw, disp, extras = _read_exr_cv2(path)
        except Exception:
            raise RuntimeError(
                f"{TAG} nao consegui ler {os.path.basename(path)}: {e}. "
                f"Instale no Python do ComfyUI: pip install \"OpenEXR>=3.3\"") from e
    if not layers:
        raise RuntimeError(f"{TAG} {os.path.basename(path)} nao tem canais legiveis.")

    used = _pick_layer(layers, layer)
    rgb, alpha = _assemble(layers[used])
    if str(window).startswith("display"):
        rgb = _place_window(rgb, dw, disp, 0.0)
        if alpha is not None:
            alpha = _place_window(alpha, dw, disp, 0.0)
    meta = {
        "layer": used or "(rgba)",
        "layers": _layer_list(layers),
        "data_window": list(dw) if dw else None,
        "display_window": list(disp) if disp else None,
        **extras,
    }
    if not use_cache:
        return rgb, alpha, meta
    _READ_CACHE.put(key, (rgb, alpha, meta))
    return rgb.copy(), (None if alpha is None else alpha.copy()), dict(meta)


# ===========================================================================
# geometria (numpy, float, SEM clamp)
# ===========================================================================
def _rotate(a, girar):
    g = str(girar or "off")
    if g.startswith("off"):
        return a
    k = -1 if g.startswith("90") else (1 if g.startswith("-90") else 2)
    return np.ascontiguousarray(np.rot90(a, k, axes=(0, 1)))


def _resize(a, tw, th):
    """a: HxW ou HxWxC float32. Area pra reduzir, bilinear pra ampliar. Sem clamp."""
    H, W = a.shape[:2]
    if tw <= 0 or th <= 0 or (tw == W and th == H):
        return a
    down = tw < W or th < H
    if cv2 is not None:
        interp = cv2.INTER_AREA if down else cv2.INTER_LINEAR
        out = cv2.resize(a, (int(tw), int(th)), interpolation=interp)
        if a.ndim == 3 and out.ndim == 2:
            out = out[..., None]
        return np.ascontiguousarray(out, np.float32)
    t = torch.from_numpy(np.ascontiguousarray(a))
    t = t[None, None] if a.ndim == 2 else t.permute(2, 0, 1)[None]
    if down:
        t = F.interpolate(t, size=(th, tw), mode="area")
    else:
        t = F.interpolate(t, size=(th, tw), mode="bilinear", align_corners=False)
    t = t[0, 0] if a.ndim == 2 else t[0].permute(1, 2, 0)
    return np.ascontiguousarray(t.numpy(), np.float32)


def apply_fit(rgb, mask, fit_mode, cx, cy, cw, ch, tw, th):
    """Espelho exato da geometria de _bx_apply_fit (o box do JS bate), sem clamp de valor."""
    H, W = rgb.shape[:2]
    fm = str(fit_mode).split()[0]
    if fm == "off":
        return rgb, mask

    if fm == "crop":
        x0 = int(round(max(0.0, min(1.0, cx)) * W))
        y0 = int(round(max(0.0, min(1.0, cy)) * H))
        cwp = int(round(max(0.01, min(1.0, cw)) * W))
        chp = int(round(max(0.01, min(1.0, ch)) * H))
        x0 = max(0, min(W - 1, x0)); y0 = max(0, min(H - 1, y0))
        cwp = max(1, min(W - x0, cwp)); chp = max(1, min(H - y0, chp))
        rgb = rgb[y0:y0 + chp, x0:x0 + cwp]
        mask = mask[y0:y0 + chp, x0:x0 + cwp]
        if tw > 0 or th > 0:
            ntw, nth = _target_from_one(cwp, chp, tw, th)
            rgb, mask = _resize(rgb, ntw, nth), _resize(mask, ntw, nth)
        return np.ascontiguousarray(rgb), np.ascontiguousarray(mask)

    if fm == "stretch":
        if tw > 0 or th > 0:
            ntw, nth = _target_from_one(W, H, tw, th)
            rgb, mask = _resize(rgb, ntw, nth), _resize(mask, ntw, nth)
        return rgb, mask

    if fm == "pad":
        if tw <= 0 and th <= 0:
            return rgb, mask
        ntw, nth = _target_from_one(W, H, tw, th)
        scale = min(ntw / W, nth / H)
        rw, rh = max(1, round(W * scale)), max(1, round(H * scale))
        rgb_r, mask_r = _resize(rgb, rw, rh), _resize(mask, rw, rh)
        canvas = np.zeros((nth, ntw, 3), np.float32)
        mcanvas = np.ones((nth, ntw), np.float32)  # padding = mascarado (igual ao Load Image)
        ox, oy = (ntw - rw) // 2, (nth - rh) // 2
        canvas[oy:oy + rh, ox:ox + rw] = rgb_r
        mcanvas[oy:oy + rh, ox:ox + rw] = mask_r
        return canvas, mcanvas

    return rgb, mask


# ===========================================================================
# OCIO
# ===========================================================================
_CFG_CACHE = _LRU(8)
_PROC_CACHE = _LRU(64)


def _config_choices():
    items = [BUILTIN]
    if os.environ.get("OCIO"):
        items.append(ENV_CFG)
    return items + _scan_input((".ocio",))


def _load_config(choice):
    """-> (config, cache_key)."""
    if not _HAS_OCIO:
        raise RuntimeError(f"{TAG} color management precisa do OpenColorIO: pip install opencolorio")
    choice = str(choice or BUILTIN)
    if choice == ENV_CFG and os.environ.get("OCIO"):
        key = ("env", os.environ["OCIO"])
        build = OCIO.Config.CreateFromEnv
    elif choice not in (BUILTIN, ENV_CFG):
        p = choice if os.path.isabs(choice) else os.path.join(_input_dir(), choice)
        if not os.path.isfile(p):
            raise FileNotFoundError(f"{TAG} config OCIO nao encontrada: {choice!r}")
        st = os.stat(p)
        key = ("file", p, st.st_mtime_ns, st.st_size)
        build = lambda: OCIO.Config.CreateFromFile(p)  # noqa: E731
    else:
        for b in ("studio-config-latest", "cg-config-latest", "ocio://default"):
            key = ("builtin", b)
            cfg = _CFG_CACHE.get(key)
            if cfg is not None:
                return cfg, key
            try:
                cfg = OCIO.Config.CreateFromBuiltinConfig(b)
            except Exception:
                continue
            _CFG_CACHE.put(key, cfg)
            return cfg, key
        raise RuntimeError(f"{TAG} nenhum config OCIO built-in disponivel nesta versao do opencolorio.")
    cfg = _CFG_CACHE.get(key)
    if cfg is None:
        cfg = build()
        _CFG_CACHE.put(key, cfg)
    return cfg, key


def _all_configs_quiet():
    out = []
    for c in _config_choices():
        try:
            out.append(_load_config(c)[0])
        except Exception:
            pass
    return out


def _union(getter):
    seen = []
    for cfg in _all_configs_quiet():
        try:
            for v in getter(cfg):
                if v not in seen:
                    seen.append(v)
        except Exception:
            pass
    return seen


def _colorspaces():
    return _union(lambda c: [cs.getName() for cs in c.getColorSpaces()])


def _displays():
    return _union(lambda c: list(c.getDisplays()))


def _views():
    return _union(lambda c: [v for d in c.getDisplays() for v in c.getViews(d)])


def _pick(items, prefs):
    for p in prefs:
        if p in items:
            return p
    return items[0] if items else ""


def _processor(cfg_choice, mode, in_cs, out_cs, display, view):
    cfg, ckey = _load_config(cfg_choice)
    if cfg.getColorSpace(in_cs) is None:
        raise RuntimeError(f"{TAG} colorspace de entrada {in_cs!r} nao existe neste config.")
    if mode == CM_CS:
        if cfg.getColorSpace(out_cs) is None:
            raise RuntimeError(f"{TAG} colorspace de saida {out_cs!r} nao existe neste config.")
        tkey = ("cs", in_cs, out_cs)
        build = lambda: cfg.getProcessor(in_cs, out_cs)  # noqa: E731
    else:
        if display not in list(cfg.getDisplays()):
            raise RuntimeError(f"{TAG} display {display!r} nao existe. Displays: {', '.join(cfg.getDisplays())}")
        valid = list(cfg.getViews(display))
        if view not in valid:
            raise RuntimeError(f"{TAG} o display {display!r} nao tem a view {view!r}. "
                               f"Views validas: {', '.join(valid)}")
        tkey = ("dv", in_cs, display, view)

        def build():
            t = OCIO.DisplayViewTransform(src=in_cs, display=display, view=view)
            return cfg.getProcessor(t)
    cpu = _PROC_CACHE.get((ckey, tkey))
    if cpu is None:
        cpu = build().getDefaultCPUProcessor()
        _PROC_CACHE.put((ckey, tkey), cpu)
    return cpu


def apply_ocio(rgb, cm):
    mode = cm.get("mode", CM_OFF)
    if mode == CM_OFF:
        return rgb, "raw (sem conversao)"
    cpu = _processor(cm.get("config"), mode, cm.get("input_colorspace"), cm.get("output_colorspace"),
                     cm.get("display"), cm.get("view"))
    buf = np.ascontiguousarray(rgb, np.float32)
    h, w = buf.shape[:2]
    cpu.apply(OCIO.PackedImageDesc(buf, w, h, 3))
    if mode == CM_CS:
        label = f"{cm.get('input_colorspace')} -> {cm.get('output_colorspace')}"
    else:
        label = f"{cm.get('input_colorspace')} -> {cm.get('display')} / {cm.get('view')}"
    return buf, label


# ===========================================================================
# pipeline
# ===========================================================================
def process(path, layer, window, unpremultiply, exposure, girar, flip_h, flip_v,
            fit_mode, crop, target_w, target_h, cm, clamp, do_fit=True, use_cache=True):
    rgb, alpha, meta = read_exr(path, layer, window, use_cache)
    has_alpha = alpha is not None
    if alpha is None:
        alpha = np.ones(rgb.shape[:2], np.float32)

    if unpremultiply and has_alpha:
        safe = np.where(np.abs(alpha) > 1e-6, alpha, 1.0)[..., None]
        rgb = np.where(np.abs(alpha[..., None]) > 1e-6, rgb / safe, rgb).astype(np.float32)

    if float(exposure) != 0.0:
        rgb = rgb * np.float32(2.0 ** float(exposure))

    rgb, alpha = _rotate(rgb, girar), _rotate(alpha, girar)
    if flip_h:
        rgb, alpha = rgb[:, ::-1], alpha[:, ::-1]
    if flip_v:
        rgb, alpha = rgb[::-1], alpha[::-1]
    rgb, alpha = np.ascontiguousarray(rgb), np.ascontiguousarray(alpha)

    # mascara na convencao do Load Image: 1 = transparente
    mask = (1.0 - np.clip(alpha, 0.0, 1.0)).astype(np.float32) if has_alpha \
        else np.zeros(alpha.shape, np.float32)
    if do_fit:
        cx, cy, cw, ch = crop
        rgb, mask = apply_fit(rgb, mask, fit_mode, float(cx), float(cy), float(cw), float(ch),
                              int(target_w), int(target_h))

    lo_hi = (float(np.nanmin(rgb)), float(np.nanmax(rgb))) if rgb.size else (0.0, 0.0)
    rgb, cm_label = apply_ocio(rgb, cm)
    # NaN/inf de render (pixel quebrado, divisao por alpha 0) contaminam qualquer
    # filtro depois -- e o clamp NAO remove NaN. Sempre saneia.
    bad = int(np.count_nonzero(~np.isfinite(rgb)))
    if bad:
        rgb = np.nan_to_num(rgb, nan=0.0, posinf=65504.0, neginf=-65504.0)
    out_lo, out_hi = (float(rgb.min()), float(rgb.max())) if rgb.size else (0.0, 0.0)
    if clamp:
        rgb = np.clip(rgb, 0.0, 1.0)
    meta.update({
        "path": path,
        "has_alpha": has_alpha,
        "linear_range_before_cm": [round(lo_hi[0], 5), round(lo_hi[1], 5)],
        "color": cm_label,
        "exposure": float(exposure),
        "clamp": bool(clamp),
        "range_after_cm": [round(out_lo, 5), round(out_hi, 5)],
        "nonfinite_fixed": bad,
    })
    return np.ascontiguousarray(rgb, np.float32), np.ascontiguousarray(mask, np.float32), meta


def _srgb_oetf(x):
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def preview_u8(rgb, cm_mode, max_side=1024):
    """So visualizacao: em modo raw aplica a curva sRGB para nao parecer escuro."""
    h, w = rgb.shape[:2]
    s = min(1.0, float(max_side) / max(h, w))
    if s < 1.0:
        rgb = _resize(rgb, max(1, round(w * s)), max(1, round(h * s)))
    v = _srgb_oetf(rgb) if cm_mode == CM_OFF else np.clip(rgb, 0.0, 1.0)
    return (np.nan_to_num(v) * 255.0 + 0.5).astype(np.uint8)


def _cm_from_values(values):
    """Aceita o dict do DynamicCombo (V3) ou valores planos (V1)."""
    flat = {}

    def walk(d):
        for k, v in d.items():
            if isinstance(v, dict):
                walk(v)
            else:
                flat[k] = v
    walk(values)
    return {
        "mode": flat.get("color_management", CM_OFF),
        "config": flat.get("ocio_config", BUILTIN),
        "input_colorspace": flat.get("input_colorspace"),
        "output_colorspace": flat.get("output_colorspace"),
        "display": flat.get("display"),
        "view": flat.get("view"),
    }


MISSING_MODES = ["erro", "repete anterior", "preto"]


def _select_frames(frames, frame_start, frame_end, nth, cap, missing):
    """-> lista [(numero, caminho|None)] na ordem de saida."""
    nums = sorted(frames)
    lo = nums[0] if int(frame_start) < 0 else int(frame_start)
    hi = nums[-1] if int(frame_end) < 0 else int(frame_end)
    if hi < lo:
        raise RuntimeError(f"{TAG} frame_end ({hi}) menor que frame_start ({lo}).")
    wanted = list(range(lo, hi + 1))[::max(1, int(nth))]
    if int(cap) > 0:
        wanted = wanted[:int(cap)]
    if not wanted:
        raise RuntimeError(f"{TAG} nenhum frame no intervalo {lo}-{hi}.")
    holes = [n for n in wanted if n not in frames]
    if holes and str(missing).startswith("erro"):
        show = ", ".join(map(str, holes[:12])) + (" ..." if len(holes) > 12 else "")
        raise RuntimeError(
            f"{TAG} faltam {len(holes)} frame(s) no intervalo {lo}-{hi}: {show}. "
            f"Sequencia no disco: {nums[0]}-{nums[-1]} ({len(nums)} arquivos). "
            f"Ajuste frame_start/frame_end ou mude missing_frames.")
    out = []
    for n in wanted:
        pth = frames.get(n)
        if pth is None and str(missing).startswith("repete"):
            prev = [k for k in nums if k < n]
            nxt = [k for k in nums if k > n]
            pth = frames[prev[-1]] if prev else (frames[nxt[0]] if nxt else None)
        out.append((n, pth))
    return out


def _interrupted():
    try:
        import comfy.model_management as mm
        mm.throw_exception_if_processing_interrupted()
    except ImportError:
        pass


def _warn_range(meta, clamp):
    lo, hi = meta.get("range_after_cm", [0, 0])
    if meta.get("nonfinite_fixed"):
        print(f"{TAG} AVISO: {meta['nonfinite_fixed']} valores NaN/inf no EXR foram trocados por 0.", flush=True)
    if not clamp and (lo < 0.0 or hi > 1.0):
        print(f"{TAG} AVISO: saida em [{lo:.6g}, {hi:.6g}], fora de 0..1 (clamp_0_1 desligado). "
              f"Varios nodes (composite, VAE encode, save) exigem 0..1: ligue clamp_0_1 "
              f"ou mantenha desligado so se o proximo node aceita HDR.", flush=True)


def run(file, exr_path, layer, exr_window, unpremultiply, exposure, clamp_0_1, cm,
        fit_mode, target_width, target_height, crop_x, crop_y, crop_w, crop_h,
        girar, flip_horizontal, flip_vertical,
        sequence=True, frame_start=-1, frame_end=-1, select_every_nth=1, frame_load_cap=0,
        missing_frames="erro"):
    if torch is None:
        raise RuntimeError(f"{TAG} torch indisponivel.")
    single, frames, label = _resolve_source(file, exr_path)
    args = dict(layer=layer, window=exr_window, unpremultiply=bool(unpremultiply),
                exposure=float(exposure), girar=girar, flip_h=bool(flip_horizontal),
                flip_v=bool(flip_vertical), fit_mode=fit_mode,
                crop=(crop_x, crop_y, crop_w, crop_h), target_w=target_width,
                target_h=target_height, cm=cm, clamp=bool(clamp_0_1))

    if not (bool(sequence) and len(frames) > 1):
        # frame unico: o do padrao cai no frame_start (ou no primeiro)
        if frames and int(frame_start) >= 0 and int(frame_start) in frames:
            single = frames[int(frame_start)]
        rgb, mask, meta = process(single, **args)
        H, W = rgb.shape[:2]
        meta.update({"width": W, "height": H, "frames": 1, "fit_mode": str(fit_mode), "girar": str(girar)})
        print(f"{TAG} {os.path.basename(single)} [{meta['layer']}] {W}x{H} | {meta['color']} | "
              f"linear {meta['linear_range_before_cm']}", flush=True)
        _warn_range(meta, clamp_0_1)
        # Sem ui/preview de execucao de proposito: o preview do node e o quadro
        # INTEIRO (rota /bruxos/exr/preview), que e onde o crop-box e desenhado.
        return (torch.from_numpy(rgb).unsqueeze(0), torch.from_numpy(mask).unsqueeze(0),
                W, H, json.dumps(meta, ensure_ascii=False, default=str), 1), None

    # ------------------------------ SEQUENCIA ------------------------------
    plan = _select_frames(frames, frame_start, frame_end, select_every_nth, frame_load_cap,
                          missing_frames)
    first_real = next((p for _n, p in plan if p), None)
    if first_real is None:
        raise RuntimeError(f"{TAG} nenhum frame existente no intervalo pedido.")
    rgb0, mask0, meta = process(first_real, **args, use_cache=False)
    H, W = rgb0.shape[:2]
    N = len(plan)
    gb = N * H * W * 4 * 4 / 1024 ** 3
    print(f"{TAG} sequencia {label}: {N} frames ({plan[0][0]}-{plan[-1][0]}) {W}x{H} "
          f"| {meta['color']} | ~{gb:.1f} GB de RAM", flush=True)

    imgs = torch.zeros((N, H, W, 3), dtype=torch.float32)
    masks = torch.zeros((N, H, W), dtype=torch.float32)
    try:
        from comfy.utils import ProgressBar
        pbar = ProgressBar(N)
    except Exception:
        pbar = None

    rng = {"lo": meta["range_after_cm"][0], "hi": meta["range_after_cm"][1],
           "bad": meta["nonfinite_fixed"]}

    def job(item):
        _n, pth = item
        if pth is None:
            return None  # 'preto'
        if pth == first_real:
            return rgb0, mask0
        r, m, mt = process(pth, **args, use_cache=False)
        rng["lo"] = min(rng["lo"], mt["range_after_cm"][0])
        rng["hi"] = max(rng["hi"], mt["range_after_cm"][1])
        rng["bad"] += mt["nonfinite_fixed"]
        return r, m

    workers = max(1, min(8, (os.cpu_count() or 4)))
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        # janela deslizante: no maximo 2*workers frames prontos esperando na RAM
        pending = {}
        it = iter(enumerate(plan))
        for _ in range(workers * 2):
            nxt = next(it, None)
            if nxt is None:
                break
            pending[nxt[0]] = ex.submit(job, nxt[1])
        i = 0
        while i < N:
            _interrupted()
            res = pending.pop(i).result()
            nxt = next(it, None)
            if nxt is not None:
                pending[nxt[0]] = ex.submit(job, nxt[1])
            if res is not None:
                r, m = res
                if r.shape[:2] != (H, W):
                    raise RuntimeError(
                        f"{TAG} frame {plan[i][0]} tem {r.shape[1]}x{r.shape[0]}, o primeiro tem {W}x{H}. "
                        f"Isso acontece com exr_window = data window quando o bbox muda por frame: "
                        f"use 'display window (Nuke)'.")
                imgs[i] = torch.from_numpy(r)
                masks[i] = torch.from_numpy(m)
            else:
                masks[i] = 1.0  # frame preto = totalmente mascarado
            i += 1
            done += 1
            if pbar is not None:
                pbar.update(1)

    holes = [n for n, p in plan if frames.get(n) is None]
    meta.update({
        "sequence": label, "frames": N, "frame_first": plan[0][0], "frame_last": plan[-1][0],
        "frames_on_disk": [min(frames), max(frames), len(frames)],
        "missing": holes[:100], "missing_mode": str(missing_frames),
        "width": W, "height": H, "fit_mode": str(fit_mode), "girar": str(girar),
    })
    meta.pop("path", None)
    meta["folder"] = os.path.dirname(first_real)
    meta["range_after_cm"] = [round(rng["lo"], 5), round(rng["hi"], 5)]
    meta["nonfinite_fixed"] = rng["bad"]
    _warn_range(meta, clamp_0_1)
    return (imgs, masks, W, H, json.dumps(meta, ensure_ascii=False, default=str), N), None


def fingerprint(file, exr_path=""):
    try:
        single, frames, _label = _resolve_source(file, exr_path)
        if frames:
            # sequencia: muda se entrar/sair frame ou algum for re-renderizado
            newest = max(os.stat(p).st_mtime_ns for p in frames.values())
            return len(frames), min(frames), max(frames), newest
        st = os.stat(single)
        return st.st_mtime_ns, st.st_size
    except Exception:
        return float("nan")


DESCRIPTION = (
    "Carrega EXR (frame ou sequencia) em float (sem clamp), com layer/AOV, janela display/data, unpremultiply, exposure, "
    "giro, flip, crop-box e fit (crop/stretch/pad) feitos em scene-linear, e color management "
    "OpenColorIO opcional: colorspace->colorspace ou display/view (tone map ACES).")

TIPS = {
    "layer": "Layer/AOV do EXR (ex.: diffuse, specular, depth). Vazio = RGBA principal. "
             "A saida 'info' lista as layers do arquivo.",
    "exr_window": "display window = enquadramento do Nuke (overscan cortado, buraco preto). "
                  "data window = todos os pixels gravados.",
    "unpremultiply": "EXR de render costuma vir premultiplicado pelo alpha. Ligue para dividir RGB pelo "
                     "alpha antes da cor (bordas limpas ao converter).",
    "exposure": "Stops em scene-linear, aplicado ANTES do OCIO. So faz sentido se o EXR e linear.",
    "clamp_0_1": "Corta a saida em 0..1 depois da cor (padrao do IMAGE no ComfyUI; composite, VAE e save "
                 "exigem). Desligue so para mandar HDR/linear a um node que aceite valores >1 e negativos.",
    "color_management": "off = valores do arquivo. colorspace = converte (ex.: ACEScg -> sRGB Encoded). "
                        "display/view = tone map de visualizacao (ex.: ACES 2.0 SDR).",
    "sequence": "Le todos os frames da sequencia (nome.1001.exr, nome.1002.exr...). Desligado = so "
                "um frame (o frame_start, ou o primeiro).",
    "frame_start": "Primeiro frame (numero do arquivo, ex.: 1001). -1 = primeiro do disco.",
    "frame_end": "Ultimo frame (inclusive). -1 = ultimo do disco.",
    "select_every_nth": "Pega 1 a cada N frames (2 = frames alternados).",
    "frame_load_cap": "Maximo de frames carregados. 0 = sem limite. Cuidado com RAM: "
                      "4K float = ~130 MB por frame.",
    "missing_frames": "Buraco na sequencia: erro (para e lista os que faltam), repete anterior "
                      "(hold, como no Nuke) ou preto.",
    "input_colorspace": "Em que espaco o EXR ESTA. Render de ACES: ACEScg. Blender/Linear 709: "
                        "'Linear Rec.709 (sRGB)'.",
}


# ===========================================================================
# NODE V3 (DynamicCombo: o color management 'abre' so quando ligado)
# ===========================================================================
_HAS_DYNCOMBO = _io is not None and hasattr(_io, "DynamicCombo")


def _geometry_inputs_v3():
    adv = True
    return [
        _io.Combo.Input("fit_mode", options=FIT_MODES, default="off (original)", advanced=adv),
        _io.Int.Input("target_width", default=0, min=0, max=16384, step=8, advanced=adv),
        _io.Int.Input("target_height", default=0, min=0, max=16384, step=8, advanced=adv),
        _io.Combo.Input("aspect", options=ASPECTS, default="livre", advanced=adv),
        _io.Float.Input("crop_x", default=0.0, min=0.0, max=1.0, step=0.001, advanced=adv),
        _io.Float.Input("crop_y", default=0.0, min=0.0, max=1.0, step=0.001, advanced=adv),
        _io.Float.Input("crop_w", default=1.0, min=0.01, max=1.0, step=0.001, advanced=adv),
        _io.Float.Input("crop_h", default=1.0, min=0.01, max=1.0, step=0.001, advanced=adv),
        _io.Combo.Input("girar", options=ROTATIONS, default="off", advanced=adv),
        _io.Boolean.Input("flip_horizontal", default=False, advanced=adv),
        _io.Boolean.Input("flip_vertical", default=False, advanced=adv),
    ]


def _cm_input_v3():
    cfgs = _config_choices()
    css = _colorspaces() or ["ACEScg"]
    disps = _displays() or ["sRGB - Display"]
    views = _views() or ["ACES 2.0 - SDR 100 nits (Rec.709)"]
    d_in = _pick(css, ["ACEScg", "Linear Rec.709 (sRGB)", "scene_linear"])
    d_out = _pick(css, ["sRGB Encoded Rec.709 (sRGB)", "sRGB - Texture", "Output - sRGB"])
    d_disp = _pick(disps, ["sRGB - Display", "sRGB"])
    d_view = _pick(views, ["ACES 2.0 - SDR 100 nits (Rec.709)", "ACES 1.0 - SDR Video", "Standard"])

    def cfg_in():
        return _io.Combo.Input("ocio_config", options=cfgs, default=BUILTIN,
                               tooltip="Config OCIO: ACES built-in, $OCIO, ou um .ocio na pasta input.")

    def in_cs():
        return _io.Combo.Input("input_colorspace", options=css, default=d_in, tooltip=TIPS["input_colorspace"])

    return _io.DynamicCombo.Input("color_management", tooltip=TIPS["color_management"], options=[
        _io.DynamicCombo.Option(CM_OFF, []),
        _io.DynamicCombo.Option(CM_CS, [
            cfg_in(), in_cs(),
            _io.Combo.Input("output_colorspace", options=css, default=d_out),
        ]),
        _io.DynamicCombo.Option(CM_DV, [
            cfg_in(), in_cs(),
            _io.Combo.Input("display", options=disps, default=d_disp),
            _io.Combo.Input("view", options=views, default=d_view,
                            tooltip="A lista traz views de todos os displays; se o par nao existir, "
                                    "o erro mostra as validas."),
        ]),
    ])


if _HAS_DYNCOMBO:
    class BruxosLoadEXR(_io.ComfyNode):
        @classmethod
        def define_schema(cls):
            files = _list_exr()
            return _io.Schema(
                node_id=NODE_ID,
                display_name="Load EXR + OCIO + Crop (Bruxos)",
                category=CAT,
                description=DESCRIPTION,
                search_aliases=["exr", "openexr", "ocio", "aces", "load exr bruxos"],
                inputs=[
                    _io.Combo.Input("file", display_name="exr",
                                    options=files if files else ["(coloque .exr em ComfyUI/input)"]),
                    _cm_input_v3(),
                    _io.Float.Input("exposure", default=0.0, min=-20.0, max=20.0, step=0.1,
                                    tooltip=TIPS["exposure"]),
                    _io.Boolean.Input("clamp_0_1", default=True, tooltip=TIPS["clamp_0_1"]),
                    _io.String.Input("layer", default="", tooltip=TIPS["layer"], advanced=True),
                    _io.Combo.Input("exr_window", options=EXR_WINDOWS, default=EXR_WINDOWS[0],
                                    tooltip=TIPS["exr_window"], advanced=True),
                    _io.Boolean.Input("unpremultiply", default=False, tooltip=TIPS["unpremultiply"],
                                      advanced=True),
                    *_geometry_inputs_v3(),
                    _io.String.Input("exr_path", default="", advanced=True,
                                     tooltip="Caminho absoluto de um frame ou padrao (shot.####.exr / "
                                             "shot.%04d.exr). Tem prioridade sobre o seletor."),
                    # APPEND-ONLY daqui pra baixo (widgets_values casam por ordem)
                    _io.Boolean.Input("sequence", default=True, tooltip=TIPS["sequence"]),
                    _io.Int.Input("frame_start", default=-1, min=-1, max=10_000_000,
                                  tooltip=TIPS["frame_start"]),
                    _io.Int.Input("frame_end", default=-1, min=-1, max=10_000_000,
                                  tooltip=TIPS["frame_end"]),
                    _io.Int.Input("select_every_nth", default=1, min=1, max=1000,
                                  tooltip=TIPS["select_every_nth"], advanced=True),
                    _io.Int.Input("frame_load_cap", default=0, min=0, max=100_000,
                                  tooltip=TIPS["frame_load_cap"]),
                    _io.Combo.Input("missing_frames", options=MISSING_MODES, default="erro",
                                    tooltip=TIPS["missing_frames"], advanced=True),
                ],
                outputs=[
                    _io.Image.Output("image"),
                    _io.Mask.Output("mask"),
                    _io.Int.Output("width"),
                    _io.Int.Output("height"),
                    _io.String.Output("info"),
                    _io.Int.Output("frame_count"),
                ],
            )

        @classmethod
        def execute(cls, file, color_management=None, exposure=0.0, clamp_0_1=True, layer="",
                    exr_window=EXR_WINDOWS[0], unpremultiply=False,
                    fit_mode="off (original)", target_width=0, target_height=0, aspect="livre",
                    crop_x=0.0, crop_y=0.0, crop_w=1.0, crop_h=1.0,
                    girar="off", flip_horizontal=False, flip_vertical=False, exr_path="",
                    sequence=True, frame_start=-1, frame_end=-1, select_every_nth=1,
                    frame_load_cap=0, missing_frames="erro"):
            cm = _cm_from_values({"color_management": color_management}
                                 if not isinstance(color_management, dict) else color_management)
            result, ui = run(file, exr_path, layer, exr_window, unpremultiply, exposure, clamp_0_1, cm,
                             fit_mode, target_width, target_height, crop_x, crop_y, crop_w, crop_h,
                             girar, flip_horizontal, flip_vertical, sequence, frame_start, frame_end,
                             select_every_nth, frame_load_cap, missing_frames)
            return _io.NodeOutput(*result, ui=ui) if ui else _io.NodeOutput(*result)

        @classmethod
        def fingerprint_inputs(cls, file, exr_path="", **_kw):
            return fingerprint(file, exr_path)


# ===========================================================================
# NODE V1 (fallback p/ ComfyUI sem DynamicCombo: tudo plano, sempre visivel)
# ===========================================================================
class BruxosLoadEXRLegacy:
    @classmethod
    def INPUT_TYPES(cls):
        files = _list_exr()
        css = _colorspaces() or ["ACEScg"]
        disps = _displays() or ["sRGB - Display"]
        views = _views() or ["ACES 2.0 - SDR 100 nits (Rec.709)"]
        inputs = {
            "required": {
                "file": (files if files else ["(coloque .exr em ComfyUI/input)"],),
            },
            "optional": {
                "color_management": (CM_MODES, {"default": CM_OFF, "tooltip": TIPS["color_management"]}),
                "ocio_config": (_config_choices(), {"default": BUILTIN}),
                "input_colorspace": (css, {"default": _pick(css, ["ACEScg"]), "tooltip": TIPS["input_colorspace"]}),
                "output_colorspace": (css, {"default": _pick(css, ["sRGB Encoded Rec.709 (sRGB)", "sRGB - Texture"])}),
                "display": (disps, {"default": _pick(disps, ["sRGB - Display"])}),
                "view": (views, {"default": _pick(views, ["ACES 2.0 - SDR 100 nits (Rec.709)"])}),
                "exposure": ("FLOAT", {"default": 0.0, "min": -20.0, "max": 20.0, "step": 0.1, "tooltip": TIPS["exposure"]}),
                "clamp_0_1": ("BOOLEAN", {"default": True, "tooltip": TIPS["clamp_0_1"]}),
                "layer": ("STRING", {"default": "", "tooltip": TIPS["layer"], "advanced": True}),
                "exr_window": (EXR_WINDOWS, {"default": EXR_WINDOWS[0], "advanced": True}),
                "unpremultiply": ("BOOLEAN", {"default": False, "advanced": True}),
                "fit_mode": (FIT_MODES, {"default": "off (original)", "advanced": True}),
                "target_width": ("INT", {"default": 0, "min": 0, "max": 16384, "step": 8, "advanced": True}),
                "target_height": ("INT", {"default": 0, "min": 0, "max": 16384, "step": 8, "advanced": True}),
                "aspect": (ASPECTS, {"default": "livre", "advanced": True}),
                "crop_x": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.001, "advanced": True}),
                "crop_y": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.001, "advanced": True}),
                "crop_w": ("FLOAT", {"default": 1.0, "min": 0.01, "max": 1.0, "step": 0.001, "advanced": True}),
                "crop_h": ("FLOAT", {"default": 1.0, "min": 0.01, "max": 1.0, "step": 0.001, "advanced": True}),
                "girar": (ROTATIONS, {"default": "off", "advanced": True}),
                "flip_horizontal": ("BOOLEAN", {"default": False, "advanced": True}),
                "flip_vertical": ("BOOLEAN", {"default": False, "advanced": True}),
                "exr_path": ("STRING", {"default": "", "advanced": True}),
                "sequence": ("BOOLEAN", {"default": True, "tooltip": TIPS["sequence"]}),
                "frame_start": ("INT", {"default": -1, "min": -1, "max": 10_000_000}),
                "frame_end": ("INT", {"default": -1, "min": -1, "max": 10_000_000}),
                "select_every_nth": ("INT", {"default": 1, "min": 1, "max": 1000, "advanced": True}),
                "frame_load_cap": ("INT", {"default": 0, "min": 0, "max": 100_000}),
                "missing_frames": (MISSING_MODES, {"default": "erro", "advanced": True}),
            },
        }
        return inputs

    RETURN_TYPES = ("IMAGE", "MASK", "INT", "INT", "STRING", "INT")
    RETURN_NAMES = ("image", "mask", "width", "height", "info", "frame_count")
    FUNCTION = "load"
    CATEGORY = CAT
    DESCRIPTION = DESCRIPTION

    def load(self, file, color_management=CM_OFF, ocio_config=BUILTIN, input_colorspace="ACEScg",
             output_colorspace="", display="", view="", exposure=0.0, clamp_0_1=True, layer="",
             exr_window=EXR_WINDOWS[0], unpremultiply=False, fit_mode="off (original)",
             target_width=0, target_height=0, aspect="livre", crop_x=0.0, crop_y=0.0,
             crop_w=1.0, crop_h=1.0, girar="off", flip_horizontal=False, flip_vertical=False,
             exr_path="", sequence=True, frame_start=-1, frame_end=-1, select_every_nth=1,
             frame_load_cap=0, missing_frames="erro"):
        cm = _cm_from_values(dict(color_management=color_management, ocio_config=ocio_config,
                                  input_colorspace=input_colorspace, output_colorspace=output_colorspace,
                                  display=display, view=view))
        result, ui = run(file, exr_path, layer, exr_window, unpremultiply, exposure, clamp_0_1, cm,
                         fit_mode, target_width, target_height, crop_x, crop_y, crop_w, crop_h,
                         girar, flip_horizontal, flip_vertical, sequence, frame_start, frame_end,
                         select_every_nth, frame_load_cap, missing_frames)
        return {"ui": ui, "result": result} if ui else result

    @classmethod
    def IS_CHANGED(cls, file, exr_path="", **_kw):
        return fingerprint(file, exr_path)


_NodeClass = BruxosLoadEXR if _HAS_DYNCOMBO else BruxosLoadEXRLegacy
NODE_CLASS_MAPPINGS = {NODE_ID: _NodeClass}
NODE_DISPLAY_NAME_MAPPINGS = {NODE_ID: "Load EXR + OCIO + Crop (Bruxos)"}


# ===========================================================================
# rota de preview p/ o crop-box (quadro INTEIRO, ja girado/flipado, sem crop)
# GET /bruxos/exr/preview?file=&exr_path=&layer=&window=&unpremult=&exposure=
#     &girar=&fh=&fv=&cm=&cfg=&in=&out=&display=&view=&max=
# ===========================================================================
try:  # pragma: no cover - so existe com o servidor ativo
    from aiohttp import web
    from server import PromptServer

    @PromptServer.instance.routes.get("/bruxos/exr/preview")
    async def _bruxos_exr_preview(request):
        import asyncio
        import io as _pyio
        from PIL import Image

        q = request.query
        root = os.path.abspath(_input_dir())

        def work():
            path, frames, label = _resolve_source(q.get("file", ""), q.get("exr_path", ""))
            seq_txt = ""
            if frames:
                try:
                    fs = int(q.get("frame", "-1") or -1)
                except ValueError:
                    fs = -1
                if fs >= 0 and fs in frames:
                    path = frames[fs]
                seq_txt = f"{min(frames)}-{max(frames)} ({len(frames)} frames)"
            if not q.get("exr_path") and os.path.commonpath((root, path)) != root:
                raise PermissionError("fora da pasta input")
            cm = {"mode": q.get("cm", CM_OFF), "config": q.get("cfg", BUILTIN),
                  "input_colorspace": q.get("in"), "output_colorspace": q.get("out"),
                  "display": q.get("display"), "view": q.get("view")}
            if cm["mode"] not in CM_MODES:
                cm["mode"] = CM_OFF
            rgb, _alpha, meta = read_exr(path, q.get("layer", ""), q.get("window", EXR_WINDOWS[0]))
            # reduz ANTES da cor: o preview so precisa de ~1k px
            mx = max(64, min(4096, int(q.get("max", "1024") or 1024)))
            h, w = rgb.shape[:2]
            g = str(q.get("girar", "off"))
            meta["_full"] = (h, w) if (g.startswith("90") or g.startswith("-90")) else (w, h)
            s = min(1.0, mx / max(h, w))
            if s < 1.0:
                rgb = _resize(rgb, max(1, round(w * s)), max(1, round(h * s)))
            ev = float(q.get("exposure", "0") or 0)
            if ev:
                rgb = rgb * np.float32(2.0 ** ev)
            rgb = _rotate(rgb, q.get("girar", "off"))
            if q.get("fh") in ("1", "true"):
                rgb = rgb[:, ::-1]
            if q.get("fv") in ("1", "true"):
                rgb = rgb[::-1]
            rgb, _ = apply_ocio(np.ascontiguousarray(rgb), cm)
            buf = _pyio.BytesIO()
            Image.fromarray(preview_u8(rgb, cm["mode"], mx)).save(buf, "PNG", compress_level=1)
            meta["_seq"] = seq_txt
            return buf.getvalue(), meta

        try:
            png, meta = await asyncio.get_running_loop().run_in_executor(None, work)
            return web.Response(body=png, content_type="image/png", headers={
                "Cache-Control": "no-store",
                "X-Bruxos-Layers": json.dumps(meta.get("layers", [])),
                "X-Bruxos-Width": str(meta["_full"][0]),
                "X-Bruxos-Height": str(meta["_full"][1]),
                "X-Bruxos-Seq": meta.get("_seq", ""),
                "Access-Control-Expose-Headers":
                    "X-Bruxos-Layers, X-Bruxos-Width, X-Bruxos-Height, X-Bruxos-Seq",
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)
except Exception:
    pass
