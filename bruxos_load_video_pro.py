# -*- coding: utf-8 -*-
"""Load Video Pro (Bruxos) -- entrada de VFX com o player Grimorio.

  * o player mostra o arquivo FONTE antes de rodar: proxy H.264 all-intra
    gerado em background (seek exato, frame a frame) + regua com IN/OUT
  * IN/OUT na regua escrevem skip_first_frames / frame_load_cap
  * crop com box arrastavel (alcas, trava de proporcao), girar e espelhar
    desenhados ao vivo, vista "resultado" e contagem de frames 4n+1 / 8n+1
  * mesmas saidas do Load Video (Bruxos): da pra trocar um pelo outro

Ordem do processamento (igual ao Load Video 2.0):
  decode (skip / nth / force_rate / cap) -> girar -> espelhar -> custom size
  -> fit (crop / stretch / pad) -> reverse.  O audio e cortado no trecho.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from fractions import Fraction

import numpy as np
import torch

from . import video_nodes as _vn
from .bruxos_load_media import ASPECTS, FIT_MODES, _bx_apply_fit, _bx_resize_img

try:
    import folder_paths
    _HAS_FP = True
except Exception:  # pragma: no cover
    folder_paths = None
    _HAS_FP = False

TAG = "[Load Video Pro Bruxos]"
ROTATIONS = ["off", "90 (horario)", "-90 (anti-horario)", "180"]

# Grades de frames dos modelos: (passo, resto) -> validos sao n % passo == resto.
# H3 segue o node Bruxos H3 Frames (template oficial do MiniMax H3: length % 17 == 5).
PAD_GRIDS = {
    "off": None,
    "Wan 4n+1": (4, 1),
    "LTX 8n+1": (8, 1),
    "H3 17n+5": (17, 5),
}


def grid_up(n: int, grid) -> int:
    """Menor contagem valida >= n na grade (passo, resto)."""
    if not grid:
        return n
    step, rest = grid
    t = n + ((rest - n) % step)
    return max(t, rest)


def pad_freeze(images: torch.Tensor, target: int) -> torch.Tensor:
    """Completa ate 'target' repetindo (congelando) o ULTIMO frame -- igual ao
    modo cortar_ou_congelar do H3 Frames."""
    extra = int(target) - int(images.shape[0])
    if extra <= 0:
        return images
    tail = images[-1:].expand(extra, *images.shape[1:])
    return torch.cat([images, tail], 0).contiguous()

# ===========================================================================
# Transformacoes
# ===========================================================================
def rotate(t: torch.Tensor, giro: str) -> torch.Tensor:
    g = str(giro or "off")
    if g.startswith("off"):
        return t
    k = -1 if g.startswith("90") else (1 if g.startswith("-90") else 2)
    return torch.rot90(t, k, (1, 2)).contiguous()


def spatial(images, girar, flip_h, flip_v, custom_w, custom_h, fit_mode,
            cx, cy, cw, ch, tw, th):
    images = rotate(images, girar)
    if flip_h:
        images = images.flip(2)
    if flip_v:
        images = images.flip(1)
    H, W = int(images.shape[1]), int(images.shape[2])
    cwid, chei = int(custom_w), int(custom_h)
    if cwid > 0 or chei > 0:
        if cwid <= 0:
            cwid = max(1, round(W * chei / H))
        if chei <= 0:
            chei = max(1, round(H * cwid / W))
        images = _bx_resize_img(images.contiguous(), cwid, chei)
    if str(fit_mode).split()[0] != "off":
        mask = torch.zeros(images.shape[:3], dtype=images.dtype)
        images, _ = _bx_apply_fit(images.contiguous(), mask, fit_mode,
                                  float(cx), float(cy), float(cw), float(ch), int(tw), int(th))
    return images.contiguous()


def slice_audio(audio, start_s, dur_s, reverse=False):
    if not audio:
        return None
    wav = audio.get("waveform")
    sr = int(audio.get("sample_rate", 0) or 0)
    if wav is None or sr <= 0:
        return None
    a = max(0, round(start_s * sr))
    n = max(0, round(dur_s * sr))
    cut = wav[..., a:a + n]
    if cut.shape[-1] < n:
        cut = torch.nn.functional.pad(cut, (0, n - cut.shape[-1]))
    if reverse and cut.shape[-1] > 1:
        cut = cut.flip(-1)
    return {"waveform": cut.contiguous(), "sample_rate": sr}


# ===========================================================================
# Caminhos
# ===========================================================================
def _input_dir():
    return folder_paths.get_input_directory() if _HAS_FP else os.path.abspath("input")


def _temp_dir():
    return folder_paths.get_temp_directory() if _HAS_FP else os.path.abspath("temp")


def _inside(base, path):
    try:
        return os.path.commonpath([os.path.abspath(base), os.path.abspath(path)]) == os.path.abspath(base)
    except ValueError:
        return False


def list_videos():
    d = _input_dir()
    out = []
    try:
        for root, _dirs, files in os.walk(d):
            for f in files:
                if f.lower().endswith(_vn.VIDEO_EXTS):
                    rel = os.path.relpath(os.path.join(root, f), d).replace("\\", "/")
                    if not rel.startswith(("clipspace/", ".")):
                        out.append(rel)
    except Exception:
        pass
    return sorted(out, key=str.lower)


def resolve(video, video_path=""):
    p = str(video_path or "").strip().strip('"')
    if p:
        if os.path.isfile(p):
            return os.path.abspath(p)
        raise FileNotFoundError(f"{TAG} video_path nao existe: {p}")
    cand = os.path.join(_input_dir(), str(video or ""))
    if os.path.isfile(cand):
        return os.path.abspath(cand)
    raise FileNotFoundError(f"{TAG} video nao encontrado: {video!r}")


# ===========================================================================
# Proxy do FONTE (all-intra) -- gerado em background, com progresso
# ===========================================================================
SRC_PROXY_MAX_SIDE = 960
SRC_PROXY_CODEC = "libx264"  # all-intra: todo frame e keyframe -> seek exato
SRC_PROXY_OPTS = {"crf": "23", "preset": "veryfast", "x264-params": "keyint=1:min-keyint=1:scenecut=0"}
STRIP_THUMBS = 32
STRIP_H = 64
_JOBS: dict = {}
_JOBS_LOCK = threading.Lock()


def _src_key(path):
    st = os.stat(path)
    return hashlib.md5(f"{os.path.abspath(path)}|{st.st_mtime_ns}|{st.st_size}|src-v2".encode()).hexdigest()[:16]


def _src_dir():
    d = os.path.join(_temp_dir(), "bruxos_pro_src")
    os.makedirs(d, exist_ok=True)
    return d


def probe(path):
    """Metadados rapidos (sem decodificar tudo)."""
    import av
    with av.open(path) as c:
        s = c.streams.video[0]
        fps = float(s.average_rate) if s.average_rate else 0.0
        if s.duration is not None and s.time_base:
            dur = float(s.duration * s.time_base)
        elif c.duration is not None:
            dur = float(c.duration / av.time_base)
        else:
            dur = 0.0
        frames = int(s.frames or round(dur * fps))
        cc = s.codec_context
        pix = str(cc.pix_fmt or "")
        bits = 10 if ("10" in pix or "p10" in pix) else (12 if "12" in pix else (16 if "16" in pix or "48" in pix else 8))
        return {
            "width": int(cc.width), "height": int(cc.height), "fps": round(fps, 6),
            "frames": frames, "duration": round(dur, 4), "codec": str(cc.name),
            "pix_fmt": pix, "bits": bits, "has_audio": bool(c.streams.audio),
            "size_mb": round(os.path.getsize(path) / 1048576, 1),
        }


def _build_src_proxy(path, key, job):
    import av
    from PIL import Image
    d = _src_dir()
    out_mp4 = os.path.join(d, f"{key}.mp4")
    out_strip = os.path.join(d, f"{key}_strip.jpg")
    out_json = os.path.join(d, f"{key}.json")
    info = probe(path)
    job["total"] = max(1, info["frames"])
    W, H = info["width"], info["height"]
    sc = min(1.0, SRC_PROXY_MAX_SIDE / max(W, H))
    pw, ph = max(2, int(round(W * sc)) // 2 * 2), max(2, int(round(H * sc)) // 2 * 2)
    tw = max(2, int(round(W * STRIP_H / H)))
    step = max(1, job["total"] // (STRIP_THUMBS * 2))
    thumbs = []
    tmp = out_mp4 + ".writing.mp4"
    n = 0
    src = av.open(path)
    dst = av.open(tmp, "w")
    try:
        vs = src.streams.video[0]
        vs.thread_type = "AUTO"
        rate = vs.average_rate or 24
        tb = Fraction(1) / Fraction(rate)
        st = dst.add_stream(SRC_PROXY_CODEC, rate=rate)
        st.width, st.height, st.pix_fmt = pw, ph, "yuv420p"
        st.options = SRC_PROXY_OPTS
        for fr in src.decode(vs):
            if job.get("cancel"):
                raise RuntimeError("cancelado")
            small = fr.reformat(width=pw, height=ph, format="yuv420p")
            # o frame decodificado carrega a base de tempo do ARQUIVO; sem isto o
            # pts=n vira microssegundos e o proxy inteiro dura 1 frame
            small.time_base = tb
            small.pts = n
            for p in st.encode(small):
                dst.mux(p)
            if n % step == 0:
                thumbs.append((n, fr.reformat(width=tw, height=STRIP_H, format="rgb24").to_ndarray()))
            n += 1
            job["done"] = n
            if n > job["total"]:
                job["total"] = n
        for p in st.encode():
            dst.mux(p)
    finally:
        dst.close()
        src.close()
    if n == 0:
        raise RuntimeError("nenhum frame decodificado")
    os.replace(tmp, out_mp4)
    # filmstrip: STRIP_THUMBS amostras espalhadas pelos frames reais
    k = min(STRIP_THUMBS, len(thumbs))
    pick = [thumbs[int(round(i * (len(thumbs) - 1) / max(1, k - 1)))] for i in range(k)] if k > 1 else thumbs[:1]
    sheet = Image.new("RGB", (tw * len(pick), STRIP_H))
    for j, (_idx, arr) in enumerate(pick):
        sheet.paste(Image.fromarray(arr), (j * tw, 0))
    sheet.save(out_strip, quality=80)
    info.update({"frames": n, "thumbs": len(pick), "proxy_w": pw, "proxy_h": ph,
                 "proxy": os.path.basename(out_mp4), "strip": os.path.basename(out_strip)})
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(info, fh)
    return info


def src_proxy_status(path):
    """{'status': 'ready'|'working'|'error', ...}. Dispara o job se precisar."""
    key = _src_key(path)
    d = _src_dir()
    js = os.path.join(d, f"{key}.json")
    if os.path.isfile(js) and os.path.isfile(os.path.join(d, f"{key}.mp4")):
        with open(js, encoding="utf-8") as fh:
            info = json.load(fh)
        return {"status": "ready", "key": key, **info}
    with _JOBS_LOCK:
        job = _JOBS.get(key)
        if job is None or job.get("status") == "error" and time.time() - job.get("t", 0) > 30:
            job = {"status": "working", "done": 0, "total": 1, "t": time.time()}
            _JOBS[key] = job

            def run():
                try:
                    _build_src_proxy(path, key, job)
                    job["status"] = "ready"
                except Exception as e:  # pragma: no cover
                    logging.warning(f"{TAG} proxy do fonte falhou: {e}")
                    job["status"] = "error"
                    job["error"] = str(e)
                    job["t"] = time.time()
            threading.Thread(target=run, daemon=True, name=f"bruxos-src-{key}").start()
    if job["status"] == "ready":
        return src_proxy_status(path)
    return {"status": job["status"], "key": key, "done": job.get("done", 0),
            "total": job.get("total", 1), "error": job.get("error")}


# ===========================================================================
# NODE
# ===========================================================================
class BruxosLoadVideoPro:
    @classmethod
    def INPUT_TYPES(cls):
        files = list_videos()
        # ORDEM IMPORTA (widgets_values e posicional): novos campos so no FIM.
        return {
            "required": {
                "video": (files or ["(coloque videos em ComfyUI/input)"],
                          {"tooltip": "Video da pasta ComfyUI/input. Use ENVIAR / GALERIA no player, ou arraste o arquivo no node."}),
            },
            "optional": {
                "video_path": ("STRING", {"default": "", "tooltip": "Caminho absoluto. Tem prioridade sobre o seletor."}),
                "skip_first_frames": ("INT", {"default": 0, "min": 0, "max": 10_000_000,
                    "tooltip": "Frame de ENTRADA (IN). Tecla I no player."}),
                "frame_load_cap": ("INT", {"default": 0, "min": 0, "max": 10_000_000,
                    "tooltip": "Quantos frames carregar (0 = ate o fim). Tecla O no player define pelo OUT."}),
                "select_every_nth": ("INT", {"default": 1, "min": 1, "max": 1000}),
                "force_rate": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 240.0, "step": 0.001,
                    "tooltip": "Reamostra para esse fps (0 = original)."}),
                "custom_width": ("INT", {"default": 0, "min": 0, "max": 16384, "step": 8}),
                "custom_height": ("INT", {"default": 0, "min": 0, "max": 16384, "step": 8}),
                "girar": (ROTATIONS, {"default": "off", "tooltip": "Antes do crop: o box ja e desenhado na orientacao final."}),
                "flip_horizontal": ("BOOLEAN", {"default": False}),
                "flip_vertical": ("BOOLEAN", {"default": False}),
                "fit_mode": (FIT_MODES, {"default": "off (original)",
                    "tooltip": "crop = usa o box do player. stretch/pad = encaixa no alvo."}),
                "aspect": (ASPECTS, {"default": "livre", "tooltip": "Trava a proporcao do box de crop."}),
                "target_width": ("INT", {"default": 0, "min": 0, "max": 16384, "step": 8}),
                "target_height": ("INT", {"default": 0, "min": 0, "max": 16384, "step": 8}),
                "crop_x": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.0001}),
                "crop_y": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.0001}),
                "crop_w": ("FLOAT", {"default": 1.0, "min": 0.01, "max": 1.0, "step": 0.0001}),
                "crop_h": ("FLOAT", {"default": 1.0, "min": 0.01, "max": 1.0, "step": 0.0001}),
                "reverse": ("BOOLEAN", {"default": False}),
                "importar_mais_bits": ("BOOLEAN", {"default": False,
                    "tooltip": "Decodifica em 16-bit (fontes 10/12-bit: ProRes, H.265 Main10)."}),
                "criar_cache_ssd": ("BOOLEAN", {"default": False,
                    "tooltip": "Cria a saida Cache (SSD81) para o Bernini."}),
                # --- append-only ---
                "pad_grade": (list(PAD_GRIDS.keys()), {"default": "off",
                    "tooltip": "Completa a contagem ate a grade do modelo CONGELANDO o ultimo frame "
                               "(ex.: 50 frames -> 53 no Wan 4n+1). Wan 4n+1, LTX 8n+1, H3 17n+5 "
                               "(5, 22, 39, 56 ... 192). O audio ganha silencio no trecho congelado."}),
            },
        }

    RETURN_TYPES = ("IMAGE", "VIDEO", "AUDIO", "FLOAT", "INT", "INT", "INT", "STRING", "BRUXOS_FRAME_CACHE")
    RETURN_NAMES = ("images", "video", "audio", "fps", "frame_count", "width", "height", "video_info", "Cache")
    FUNCTION = "load"
    CATEGORY = "Bruxos do VFX/Video"

    @classmethod
    def IS_CHANGED(cls, video, video_path="", **_kw):
        try:
            p = resolve(video, video_path)
            st = os.stat(p)
            return f"{p}|{st.st_mtime_ns}|{st.st_size}"
        except Exception:
            return float("nan")

    @classmethod
    def VALIDATE_INPUTS(cls, video, video_path="", **_kw):
        if str(video_path or "").strip():
            return True
        if not os.path.isfile(os.path.join(_input_dir(), str(video or ""))):
            return f"video nao encontrado em input: {video}"
        return True

    def load(self, video, video_path="", skip_first_frames=0, frame_load_cap=0, select_every_nth=1,
             force_rate=0.0, custom_width=0, custom_height=0, girar="off", flip_horizontal=False,
             flip_vertical=False, fit_mode="off (original)", aspect="livre", target_width=0,
             target_height=0, crop_x=0.0, crop_y=0.0, crop_w=1.0, crop_h=1.0, reverse=False,
             importar_mais_bits=False, criar_cache_ssd=False, pad_grade="off"):
        path = resolve(video, video_path)
        t0 = time.time()
        decode = _vn.decode_video_hi if importar_mais_bits else _vn.decode_video
        images, src_fps, out_fps = decode(path, int(skip_first_frames), int(frame_load_cap),
                                          int(select_every_nth), float(force_rate), 0, 0)
        src_h, src_w = int(images.shape[1]), int(images.shape[2])
        images = spatial(images, girar, flip_horizontal, flip_vertical, custom_width, custom_height,
                         fit_mode, crop_x, crop_y, crop_w, crop_h, target_width, target_height)
        if reverse and images.shape[0] > 1:
            images = images.flip(0).contiguous()
        B0 = int(images.shape[0])
        fps = float(out_fps if out_fps > 0 else (src_fps or 24.0))

        # ---- pad na grade do modelo (congela o ultimo frame) ----
        grid = PAD_GRIDS.get(str(pad_grade))
        target = grid_up(B0, grid)
        images = pad_freeze(images, target)
        pad = target - B0
        B, H, W = int(images.shape[0]), int(images.shape[1]), int(images.shape[2])

        audio = _vn._extract_audio_av(path)
        if audio is not None:
            start = (int(skip_first_frames) / src_fps) if src_fps else 0.0
            audio = slice_audio(audio, start, B0 / fps, bool(reverse))
            if pad and audio is not None:  # silencio durante o freeze
                sr = audio["sample_rate"]
                want = round(B / fps * sr)
                wav = audio["waveform"]
                if wav.shape[-1] < want:
                    audio = {"waveform": torch.nn.functional.pad(wav, (0, want - wav.shape[-1])), "sample_rate": sr}
        video_obj = _vn._make_video_obj(images, audio, fps)

        info = {
            "source_path": path, "source_fps": round(src_fps, 4), "output_fps": round(fps, 4),
            "source_size": [src_w, src_h], "frame_count": B, "width": W, "height": H,
            "in": int(skip_first_frames),
            "out": int(skip_first_frames) + max(0, (B0 - 1)) * (1 if force_rate else int(select_every_nth)),
            "pad_grade": str(pad_grade), "pad_frames": pad, "frames_before_pad": B0,
            "girar": girar, "flip": [bool(flip_horizontal), bool(flip_vertical)],
            "fit_mode": fit_mode, "crop": [round(float(v), 4) for v in (crop_x, crop_y, crop_w, crop_h)],
            "reverse": bool(reverse), "has_audio": audio is not None,
            "seconds": round(time.time() - t0, 2),
        }

        cache_path = ""
        if criar_cache_ssd:
            try:
                st = os.stat(path)
                key = hashlib.sha1(json.dumps({**{k: info[k] for k in ("in", "girar", "flip", "fit_mode", "crop", "reverse")},
                                               "p": path, "m": st.st_mtime_ns, "cap": int(frame_load_cap),
                                               "nth": int(select_every_nth), "fr": float(force_rate),
                                               "cw": int(custom_width), "ch": int(custom_height),
                                               "tw": int(target_width), "th": int(target_height),
                                               "hi": bool(importar_mais_bits), "pad": [str(pad_grade), pad]}, sort_keys=True).encode()).hexdigest()[:12]
                stem = os.path.splitext(os.path.basename(path))[0]
                from .bruxos_disk_stream import BruxosImagensParaDisco
                cache_path, _, _, cache_info = BruxosImagensParaDisco().run(images, f"load_pro_{stem}_{key}", 81, fps, False)
                info["cache_ssd_81"] = cache_path
                info["cache_info"] = cache_info
            except Exception as exc:
                raise RuntimeError(f"{TAG} nao foi possivel criar o Cache SSD81: {exc}") from exc

        info_json = json.dumps(info, ensure_ascii=False)
        print(f"{TAG} {os.path.basename(path)}: {B} frames {W}x{H} @ {fps:g}fps "
              f"(in {info['in']} -> out {info['out']}{f', +{pad} freeze ({pad_grade})' if pad else ''}) "
              f"em {info['seconds']}s", flush=True)
        ui = {"bruxos_load_pro": [{"frames": B, "width": W, "height": H, "fps": fps,
                                   "in": info["in"], "out": info["out"], "has_audio": audio is not None,
                                   "seconds": info["seconds"], "pad": pad, "pad_grade": str(pad_grade)}]}
        return {"ui": ui, "result": (images, video_obj, audio, fps, B, W, H, info_json, cache_path)}


BruxosLoadVideoPro.DESCRIPTION = (
    "Load Video Pro (Bruxos) -- carrega video com o player Grimorio: ve o arquivo ANTES de "
    "rodar, frame a frame; IN/OUT na regua (I / O), crop com box e trava de proporcao, girar, "
    "espelhar, vista do resultado, contagem 4n+1/8n+1, galeria e upload por arrastar. "
    "Mesmas saidas do Load Video (Bruxos); o audio sai cortado no trecho."
)

NODE_CLASS_MAPPINGS = {"BruxosLoadVideoPro": BruxosLoadVideoPro}
NODE_DISPLAY_NAME_MAPPINGS = {"BruxosLoadVideoPro": "🧙 Load Video Pro (Bruxos)"}


# ===========================================================================
# Rotas
# ===========================================================================
try:
    from aiohttp import web
    from server import PromptServer

    def _path_from_query(q):
        vp = str(q.get("path", "") or "").strip().strip('"')
        if vp:
            # caminho absoluto: so arquivos de VIDEO (a rota so devolve um proxy)
            if not vp.lower().endswith(_vn.VIDEO_EXTS) or not os.path.isfile(vp):
                return None
            return os.path.abspath(vp)
        p = os.path.abspath(os.path.join(_input_dir(), str(q.get("filename", ""))))
        if not _inside(_input_dir(), p) or not os.path.isfile(p):
            return None
        return p

    @PromptServer.instance.routes.get("/bruxos/pro/src")
    async def _bruxos_pro_src(request):  # pragma: no cover
        p = _path_from_query(request.query)
        if not p:
            return web.json_response({"status": "error", "error": "arquivo invalido"}, status=400)
        try:
            return web.json_response(src_proxy_status(p))
        except Exception as e:
            return web.json_response({"status": "error", "error": str(e)}, status=500)

    @PromptServer.instance.routes.get("/bruxos/pro/src_file")
    async def _bruxos_pro_src_file(request):  # pragma: no cover
        key = str(request.query.get("key", ""))
        kind = str(request.query.get("kind", "mp4"))
        if not key.isalnum() or kind not in ("mp4", "strip"):
            return web.json_response({"error": "invalido"}, status=400)
        f = os.path.join(_src_dir(), f"{key}.mp4" if kind == "mp4" else f"{key}_strip.jpg")
        if not os.path.isfile(f):
            return web.json_response({"error": "nao existe"}, status=404)
        return web.FileResponse(f, headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @PromptServer.instance.routes.get("/bruxos/pro/videos")
    async def _bruxos_pro_videos(request):  # pragma: no cover
        return web.json_response(list_videos())
except Exception:  # pragma: no cover
    pass
