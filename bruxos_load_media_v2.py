# -*- coding: utf-8 -*-
"""Loaders Bruxos 2.0.

Variantes de teste que usam a API VIDEO nativa do ComfyUI como fonte unica.
O video neutro usa o preview oficial; transformacoes materializam somente um
proxy visual H.264 em cache, sem substituir o master nas saidas.
"""

from __future__ import annotations

import json
import hashlib
import math
import os
from fractions import Fraction

import torch

try:
    import folder_paths
except Exception:  # pragma: no cover
    folder_paths = None

try:
    from comfy_api.latest import (
        InputImpl as _InputImpl,
        Types as _Types,
        io as _io,
        ui as _ui,
    )
except Exception:  # pragma: no cover
    _InputImpl = None
    _Types = None
    _io = None
    _ui = None

from .bruxos_load_media import (
    ASPECTS,
    FIT_MODES,
    IMG_EXTS,
    _bx_apply_fit,
    _bx_resize_img,
    _list_input_images,
    _read_image_rgba,
    _resolve_image_path,
)


VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".avi", ".webm", ".gif", ".m4v", ".mpg", ".mpeg", ".wmv", ".flv")
ROTATIONS = ["off", "90 (horario)", "-90 (anti-horario)", "180"]
CAT = "Bruxos do VFX/Loaders 2.0 (teste)"


def _input_dir():
    if folder_paths is None:
        return os.getcwd()
    return folder_paths.get_input_directory()


def _list_input_videos():
    root = _input_dir()
    result = []
    try:
        for base, _dirs, files in os.walk(root):
            for name in files:
                if name.lower().endswith(VIDEO_EXTS):
                    result.append(os.path.relpath(os.path.join(base, name), root).replace("\\", "/"))
    except Exception:
        pass
    return sorted(result)


def _resolve_video_path(video, video_path=""):
    if str(video_path or "").strip():
        path = os.path.abspath(str(video_path).strip().strip('"'))
        if os.path.isfile(path):
            return path
    path = os.path.abspath(os.path.join(_input_dir(), str(video or "")))
    if os.path.isfile(path):
        return path
    raise FileNotFoundError(f"[Bruxos Load Video 2.0] video nao encontrado: {video!r}")


def _rotate(tensor, rotation, image=True):
    value = str(rotation or "off")
    if value.startswith("off"):
        return tensor
    k = -1 if value.startswith("90") else (1 if value.startswith("-90") else 2)
    dims = (1, 2) if image else (-2, -1)
    return torch.rot90(tensor, k, dims).contiguous()


def _spatial_transform(images, rotation, flip_horizontal, flip_vertical,
                       custom_width, custom_height, fit_mode,
                       crop_x, crop_y, crop_w, crop_h, target_width, target_height):
    images = _rotate(images, rotation)
    if bool(flip_horizontal):
        images = images.flip(2).contiguous()
    if bool(flip_vertical):
        images = images.flip(1).contiguous()

    height, width = int(images.shape[1]), int(images.shape[2])
    cw, ch = int(custom_width), int(custom_height)
    if cw > 0 or ch > 0:
        if cw <= 0:
            cw = max(1, round(width * ch / height))
        if ch <= 0:
            ch = max(1, round(height * cw / width))
        images = _bx_resize_img(images, cw, ch).contiguous()

    if str(fit_mode).split()[0] != "off":
        mask = torch.zeros(images.shape[:3], dtype=images.dtype, device=images.device)
        images, _ = _bx_apply_fit(
            images, mask, fit_mode,
            float(crop_x), float(crop_y), float(crop_w), float(crop_h),
            int(target_width), int(target_height),
        )
    return images.contiguous()


def _slice_audio(audio, start_seconds, duration_seconds, reverse=False):
    if not audio:
        return None
    waveform = audio.get("waveform")
    sample_rate = int(audio.get("sample_rate", 0) or 0)
    if waveform is None or sample_rate <= 0:
        return None
    start = max(0, round(float(start_seconds) * sample_rate))
    wanted = max(0, round(float(duration_seconds) * sample_rate))
    clipped = waveform[..., start:start + wanted]
    if clipped.shape[-1] < wanted:
        clipped = torch.nn.functional.pad(clipped, (0, wanted - clipped.shape[-1]))
    if reverse and clipped.shape[-1] > 1:
        clipped = clipped.flip(-1).contiguous()
    return {"waveform": clipped.contiguous(), "sample_rate": sample_rate}


def _temporal_select(images, source_fps, skip, cap, nth, force_rate):
    total = int(images.shape[0])
    skip = min(max(0, int(skip)), total)
    cap = max(0, int(cap))
    nth = max(1, int(nth))
    source_fps = float(source_fps or 1.0)

    if skip >= total:
        raise RuntimeError("[Bruxos Load Video 2.0] nenhum frame depois de skip_first_frames.")

    if float(force_rate) > 0:
        output_fps = float(force_rate)
        available_duration = (total - skip) / source_fps
        output_count = max(1, int(math.ceil(available_duration * output_fps - 1e-9)))
        if cap:
            output_count = min(output_count, cap)
        timeline = torch.arange(output_count, dtype=torch.float64) / output_fps
        indices = (timeline * source_fps).floor().long().add(skip).clamp(max=total - 1)
    else:
        output_fps = source_fps / nth
        indices = torch.arange(skip, total, nth, dtype=torch.long)
        if cap:
            indices = indices[:cap]

    selected = images.index_select(0, indices.to(images.device)).contiguous()
    duration = int(selected.shape[0]) / output_fps
    start_seconds = skip / source_fps
    return selected, output_fps, start_seconds, duration


def _advanced(inputs):
    for spec in inputs["optional"].values():
        if len(spec) > 1 and isinstance(spec[1], dict):
            spec[1]["advanced"] = True
    return inputs


def _preview_ref(path, folder_type):
    if folder_paths is None:
        return None
    base = (folder_paths.get_temp_directory()
            if folder_type == "temp" else folder_paths.get_input_directory())
    subfolder = os.path.relpath(os.path.dirname(path), base)
    return {
        "filename": os.path.basename(path),
        "subfolder": "" if subfolder == "." else subfolder,
        "type": folder_type,
        "format": "video/mp4",
    }


def _needs_proxy(video_path, force_rate, custom_width, custom_height,
                 frame_load_cap, skip_first_frames, select_every_nth, reverse,
                 fit_mode, rotation, flip_horizontal, flip_vertical):
    return any((
        bool(str(video_path or "").strip()),
        float(force_rate) > 0,
        int(custom_width) > 0,
        int(custom_height) > 0,
        int(frame_load_cap) > 0,
        int(skip_first_frames) > 0,
        int(select_every_nth) > 1,
        bool(reverse),
        not str(fit_mode).startswith("off"),
        not str(rotation).startswith("off"),
        bool(flip_horizontal),
        bool(flip_vertical),
    ))


def _transformed_proxy(images, source_path, output_fps, options):
    """Materializa somente a visualizacao; as saidas usam o tensor original."""
    if folder_paths is None:
        return None
    from .bruxos_save_video_v2 import BruxosSaveVideoV2

    stat = os.stat(source_path)
    signature = json.dumps({
        "path": os.path.abspath(source_path),
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
        "fps": round(float(output_fps), 8),
        "shape": tuple(int(v) for v in images.shape),
        "options": options,
        "preview_version": 1,
    }, sort_keys=True, ensure_ascii=True)
    key = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:20]
    out_dir = folder_paths.get_temp_directory()
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"bruxos_load_v2_{key}.mp4")

    try:
        if os.path.isfile(out_path):
            BruxosSaveVideoV2._validate_video(out_path)
            return out_path
    except Exception:
        try:
            os.remove(out_path)
        except OSError:
            pass

    array = (
        images[..., :3].detach().clamp(0, 1)
        .mul(255).round().to(dtype=torch.uint8, device="cpu").numpy()
    )
    frames = [array[index] for index in range(array.shape[0])]
    frames = BruxosSaveVideoV2._proxy_frames(frames, 960)
    BruxosSaveVideoV2._encode_atomic(
        frames, out_path, float(output_fps), "h264", 30, "yuv420p"
    )
    return out_path


class BruxosLoadImageV2:
    @classmethod
    def INPUT_TYPES(cls):
        files = _list_input_images()
        return _advanced({
            "required": {
                "image": (files if files else ["(coloque imagens em ComfyUI/input)"],),
            },
            "optional": {
                "fit_mode": (FIT_MODES, {"default": "off (original)"}),
                "target_width": ("INT", {"default": 0, "min": 0, "max": 16384, "step": 8}),
                "target_height": ("INT", {"default": 0, "min": 0, "max": 16384, "step": 8}),
                "aspect": (ASPECTS, {"default": "livre"}),
                "crop_x": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.001}),
                "crop_y": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.001}),
                "crop_w": ("FLOAT", {"default": 1.0, "min": 0.01, "max": 1.0, "step": 0.001}),
                "crop_h": ("FLOAT", {"default": 1.0, "min": 0.01, "max": 1.0, "step": 0.001}),
                "girar": (ROTATIONS, {"default": "off"}),
                "flip_horizontal": ("BOOLEAN", {"default": False}),
                "flip_vertical": ("BOOLEAN", {"default": False}),
                "image_path": ("STRING", {"default": ""}),
            },
        })

    RETURN_TYPES = ("IMAGE", "MASK", "INT", "INT")
    RETURN_NAMES = ("image", "mask", "width", "height")
    FUNCTION = "load"
    CATEGORY = CAT
    DESCRIPTION = "Variante 2.0: preview unico, crop persistente e sem reordenar widgets."

    def load(self, image, fit_mode="off (original)", target_width=0, target_height=0,
             aspect="livre", crop_x=0.0, crop_y=0.0, crop_w=1.0, crop_h=1.0,
             girar="off", flip_horizontal=False, flip_vertical=False, image_path=""):
        path = _resolve_image_path(image, image_path)
        rgb, mask = _read_image_rgba(path)
        images = torch.from_numpy(rgb).unsqueeze(0)
        masks = torch.from_numpy(mask).unsqueeze(0)

        images = _rotate(images, girar)
        masks = _rotate(masks, girar, image=False)
        if bool(flip_horizontal):
            images, masks = images.flip(2), masks.flip(2)
        if bool(flip_vertical):
            images, masks = images.flip(1), masks.flip(1)
        images, masks = _bx_apply_fit(
            images.contiguous(), masks.contiguous(), fit_mode,
            float(crop_x), float(crop_y), float(crop_w), float(crop_h),
            int(target_width), int(target_height),
        )
        height, width = int(images.shape[1]), int(images.shape[2])
        return images.contiguous(), masks.contiguous(), width, height

    @classmethod
    def IS_CHANGED(cls, image, image_path="", **_kwargs):
        try:
            return os.path.getmtime(_resolve_image_path(image, image_path))
        except Exception:
            return float("nan")


class BruxosLoadVideoV2:
    @classmethod
    def INPUT_TYPES(cls):
        files = _list_input_videos()
        return _advanced({
            "required": {
                "video": (
                    files if files else ["(coloque videos em ComfyUI/input)"],
                    {"video_upload": True},
                ),
            },
            "optional": {
                "video_path": ("STRING", {"default": ""}),
                "force_rate": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 240.0, "step": 0.01}),
                "custom_width": ("INT", {"default": 0, "min": 0, "max": 16384, "step": 8}),
                "custom_height": ("INT", {"default": 0, "min": 0, "max": 16384, "step": 8}),
                "frame_load_cap": ("INT", {"default": 0, "min": 0, "max": 1000000}),
                "skip_first_frames": ("INT", {"default": 0, "min": 0, "max": 1000000}),
                "select_every_nth": ("INT", {"default": 1, "min": 1, "max": 1000}),
                "reverse": ("BOOLEAN", {"default": False}),
                "fit_mode": (FIT_MODES, {"default": "off (original)"}),
                "target_width": ("INT", {"default": 0, "min": 0, "max": 16384, "step": 8}),
                "target_height": ("INT", {"default": 0, "min": 0, "max": 16384, "step": 8}),
                "aspect": (ASPECTS, {"default": "livre"}),
                "crop_x": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.001}),
                "crop_y": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.001}),
                "crop_w": ("FLOAT", {"default": 1.0, "min": 0.01, "max": 1.0, "step": 0.001}),
                "crop_h": ("FLOAT", {"default": 1.0, "min": 0.01, "max": 1.0, "step": 0.001}),
                "girar": (ROTATIONS, {"default": "off"}),
                "flip_horizontal": ("BOOLEAN", {"default": False}),
                "flip_vertical": ("BOOLEAN", {"default": False}),
                "sync_group": ("STRING", {"default": "", "tooltip": "Preencha o mesmo nome em videos que devem tocar sincronizados."}),
            },
        })

    RETURN_TYPES = ("IMAGE", "VIDEO", "AUDIO", "FLOAT", "INT", "INT", "INT", "STRING")
    RETURN_NAMES = ("images", "video", "audio", "fps", "frame_count", "width", "height", "video_info")
    FUNCTION = "load"
    CATEGORY = CAT
    DESCRIPTION = (
        "VideoFromFile nativo e preview oficial do ComfyUI quando nao ha transformacao. "
        "Crop, rotacao, flip, reverse, resize ou corte temporal geram somente um proxy "
        "visual H.264 em cache; as saidas continuam vindo do master."
    )

    def load(self, video, video_path="", force_rate=0.0, custom_width=0, custom_height=0,
             frame_load_cap=0, skip_first_frames=0, select_every_nth=1, reverse=False,
             fit_mode="off (original)", target_width=0, target_height=0,
             aspect="livre", crop_x=0.0, crop_y=0.0, crop_w=1.0, crop_h=1.0,
             girar="off", flip_horizontal=False, flip_vertical=False, sync_group=""):
        del aspect, sync_group
        if _InputImpl is None or _Types is None:
            raise RuntimeError("[Bruxos Load Video 2.0] esta versao requer a API VIDEO nativa do ComfyUI.")

        path = _resolve_video_path(video, video_path)
        source = _InputImpl.VideoFromFile(path)
        components = source.get_components()
        source_fps = float(components.frame_rate or 1.0)
        images, output_fps, start_seconds, duration = _temporal_select(
            components.images, source_fps, skip_first_frames, frame_load_cap,
            select_every_nth, force_rate,
        )
        images = _spatial_transform(
            images, girar, flip_horizontal, flip_vertical,
            custom_width, custom_height, fit_mode,
            crop_x, crop_y, crop_w, crop_h, target_width, target_height,
        )
        audio = _slice_audio(components.audio, start_seconds, duration, bool(reverse))
        if bool(reverse) and images.shape[0] > 1:
            images = images.flip(0).contiguous()

        final_components = _Types.VideoComponents(
            images=images,
            audio=audio,
            frame_rate=Fraction(output_fps).limit_denominator(100000),
            metadata=components.metadata,
        )
        video_object = _InputImpl.VideoFromComponents(final_components)
        frame_count, height, width = int(images.shape[0]), int(images.shape[1]), int(images.shape[2])
        info = {
            "source_path": path,
            "source_fps": round(source_fps, 6),
            "output_fps": round(output_fps, 6),
            "frame_count": frame_count,
            "width": width,
            "height": height,
            "duration": round(duration, 6),
            "audio_synced": audio is not None,
            "native_source": True,
        }
        result = (
            images, video_object, audio, float(output_fps), frame_count,
            width, height, json.dumps(info, ensure_ascii=False),
        )
        if not _needs_proxy(
            video_path, force_rate, custom_width, custom_height,
            frame_load_cap, skip_first_frames, select_every_nth, reverse,
            fit_mode, girar, flip_horizontal, flip_vertical,
        ):
            # video_upload deixa o frontend cuidar do componente nativo oficial.
            return result

        proxy_options = {
            "force_rate": float(force_rate),
            "custom_width": int(custom_width),
            "custom_height": int(custom_height),
            "frame_load_cap": int(frame_load_cap),
            "skip_first_frames": int(skip_first_frames),
            "select_every_nth": int(select_every_nth),
            "reverse": bool(reverse),
            "fit_mode": str(fit_mode),
            "target_width": int(target_width),
            "target_height": int(target_height),
            "crop": [float(crop_x), float(crop_y), float(crop_w), float(crop_h)],
            "girar": str(girar),
            "flip_horizontal": bool(flip_horizontal),
            "flip_vertical": bool(flip_vertical),
        }
        proxy_path = _transformed_proxy(images, path, output_fps, proxy_options)
        ref = _preview_ref(proxy_path, "temp") if proxy_path else None
        if ref:
            return {
                "ui": {"images": [ref], "animated": (True,)},
                "result": result,
            }
        return result

    @classmethod
    def IS_CHANGED(cls, video, video_path="", **_kwargs):
        try:
            return os.path.getmtime(_resolve_video_path(video, video_path))
        except Exception:
            return float("nan")


class BruxosLoadVideoV2Native(_io.ComfyNode):
    """Schema 2.0 real; delega apenas o processamento para a classe testada acima."""

    @classmethod
    def define_schema(cls):
        files = _list_input_videos()
        advanced = True
        return _io.Schema(
            node_id="BruxosLoadVideoV2",
            display_name="Load Video 2.0 (Bruxos TESTE)",
            category=CAT,
            description=BruxosLoadVideoV2.DESCRIPTION,
            search_aliases=["load video bruxos", "import video bruxos"],
            inputs=[
                # Mesmo contrato do LoadVideo oficial. display_name preserva o
                # rotulo antigo, mas o id interno 'file' evita qualquer caminho
                # especial legado associado ao antigo combo 'video'.
                _io.Combo.Input(
                    "file",
                    display_name="video",
                    options=files,
                    upload=_io.UploadType.video,
                ),
                _io.String.Input("video_path", default="", advanced=advanced),
                _io.Float.Input("force_rate", default=0.0, min=0.0, max=240.0, step=0.01, advanced=advanced),
                _io.Int.Input("custom_width", default=0, min=0, max=16384, step=8, advanced=advanced),
                _io.Int.Input("custom_height", default=0, min=0, max=16384, step=8, advanced=advanced),
                _io.Int.Input("frame_load_cap", default=0, min=0, max=1000000, advanced=advanced),
                _io.Int.Input("skip_first_frames", default=0, min=0, max=1000000, advanced=advanced),
                _io.Int.Input("select_every_nth", default=1, min=1, max=1000, advanced=advanced),
                _io.Boolean.Input("reverse", default=False, advanced=advanced),
                _io.Combo.Input("fit_mode", options=FIT_MODES, default="off (original)", advanced=advanced),
                _io.Int.Input("target_width", default=0, min=0, max=16384, step=8, advanced=advanced),
                _io.Int.Input("target_height", default=0, min=0, max=16384, step=8, advanced=advanced),
                _io.Combo.Input("aspect", options=ASPECTS, default="livre", advanced=advanced),
                _io.Float.Input("crop_x", default=0.0, min=0.0, max=1.0, step=0.001, advanced=advanced),
                _io.Float.Input("crop_y", default=0.0, min=0.0, max=1.0, step=0.001, advanced=advanced),
                _io.Float.Input("crop_w", default=1.0, min=0.01, max=1.0, step=0.001, advanced=advanced),
                _io.Float.Input("crop_h", default=1.0, min=0.01, max=1.0, step=0.001, advanced=advanced),
                _io.Combo.Input("girar", options=ROTATIONS, default="off", advanced=advanced),
                _io.Boolean.Input("flip_horizontal", default=False, advanced=advanced),
                _io.Boolean.Input("flip_vertical", default=False, advanced=advanced),
                _io.String.Input(
                    "sync_group",
                    default="",
                    advanced=advanced,
                    tooltip="Reservado para sincronizacao; o preview nativo e administrado pelo ComfyUI.",
                ),
            ],
            outputs=[
                _io.Image.Output("images"),
                _io.Video.Output("video"),
                _io.Audio.Output("audio"),
                _io.Float.Output("fps"),
                _io.Int.Output("frame_count"),
                _io.Int.Output("width"),
                _io.Int.Output("height"),
                _io.String.Output("video_info"),
            ],
        )

    @classmethod
    def execute(
        cls, file, video_path="", force_rate=0.0, custom_width=0, custom_height=0,
        frame_load_cap=0, skip_first_frames=0, select_every_nth=1, reverse=False,
        fit_mode="off (original)", target_width=0, target_height=0,
        aspect="livre", crop_x=0.0, crop_y=0.0, crop_w=1.0, crop_h=1.0,
        girar="off", flip_horizontal=False, flip_vertical=False, sync_group="",
    ):
        raw = BruxosLoadVideoV2().load(
            file, video_path, force_rate, custom_width, custom_height,
            frame_load_cap, skip_first_frames, select_every_nth, reverse,
            fit_mode, target_width, target_height, aspect,
            crop_x, crop_y, crop_w, crop_h, girar,
            flip_horizontal, flip_vertical, sync_group,
        )
        if isinstance(raw, dict):
            values = raw.get("result", ())
            refs = (raw.get("ui") or {}).get("images") or []
            if refs:
                return _io.NodeOutput(*values, ui=_ui.PreviewVideo(refs))
            return _io.NodeOutput(*values)
        return _io.NodeOutput(*raw)

    @classmethod
    def fingerprint_inputs(cls, file, video_path="", **_kwargs):
        try:
            path = _resolve_video_path(file, video_path)
            stat = os.stat(path)
            return stat.st_mtime_ns, stat.st_size
        except Exception:
            return float("nan")


NODE_CLASS_MAPPINGS = {
    "BruxosLoadImageV2": BruxosLoadImageV2,
    "BruxosLoadVideoV2": BruxosLoadVideoV2Native,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BruxosLoadImageV2": "Load Image + Crop 2.0 (Bruxos TESTE)",
    "BruxosLoadVideoV2": "Load Video 2.0 (Bruxos TESTE)",
}


# Metadados leves para a timeline do preview. Usa o mesmo PyAV da API nativa
# e nunca decodifica/reencoda frames. Restrito ao diretorio input do ComfyUI.
try:  # pragma: no cover - so existe enquanto o servidor ComfyUI esta ativo
    import av
    from aiohttp import web
    from server import PromptServer

    @PromptServer.instance.routes.get("/bruxos/v2/video_info")
    async def _bruxos_v2_video_info(request):
        filename = request.query.get("filename", "")
        subfolder = request.query.get("subfolder", "")
        root = os.path.abspath(_input_dir())
        path = os.path.abspath(os.path.join(root, subfolder, filename))
        try:
            if os.path.commonpath((root, path)) != root or not os.path.isfile(path):
                return web.json_response({"error": "arquivo invalido"}, status=400)
            with av.open(path, mode="r") as container:
                stream = container.streams.video[0]
                fps = float(stream.average_rate) if stream.average_rate else 0.0
                if stream.duration is not None:
                    duration = float(stream.duration * stream.time_base)
                elif container.duration is not None:
                    duration = float(container.duration / av.time_base)
                else:
                    duration = 0.0
                frames = int(stream.frames or round(duration * fps))
                return web.json_response({
                    "width": int(stream.width),
                    "height": int(stream.height),
                    "fps": round(fps, 8),
                    "frame_count": frames,
                    "duration": round(duration, 8),
                })
        except Exception as error:
            return web.json_response({"error": str(error)}, status=500)
except Exception:
    pass
