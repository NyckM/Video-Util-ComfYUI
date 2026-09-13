"""Save Video 2.0 (Bruxos TESTE).

Mantem o arquivo master independente do preview. O master pode ser H.265,
ProRes, VP9, 10-bit ou 4:2:2/4:4:4; o preview padrao e um proxy H.264 8-bit
4:2:0 pequeno, entregue pelo contrato nativo de preview do ComfyUI.
"""

from __future__ import annotations

import logging
import os
import re
import uuid

import numpy as np
from PIL import Image

from . import video_nodes as _video


class BruxosSaveVideoV2(_video.BruxosSaveVideo):
    @classmethod
    def INPUT_TYPES(cls):
        base = _video.BruxosSaveVideo.INPUT_TYPES()
        optional = dict(base.get("optional", {}))
        optional.update(
            {
                "preview_mode": (
                    [
                        "proxy H.264 (recomendado)",
                        "arquivo original",
                        "somente thumbnail",
                    ],
                    {
                        "default": "proxy H.264 (recomendado)",
                        "tooltip": (
                            "Proxy: preview leve e compativel sem alterar o master. "
                            "Original: tenta tocar o master no navegador. Thumbnail: imagem estatica."
                        ),
                    },
                ),
                "preview_max_side": (
                    "INT",
                    {
                        "default": 960,
                        "min": 320,
                        "max": 1920,
                        "step": 16,
                        "tooltip": "Maior lado do proxy. Nao altera o arquivo final.",
                    },
                ),
                "preview_crf": (
                    "INT",
                    {
                        "default": 30,
                        "min": 18,
                        "max": 40,
                        "step": 1,
                        "tooltip": "Qualidade do proxy. Menor = melhor e maior.",
                    },
                ),
            }
        )
        return {"required": dict(base["required"]), "optional": optional}

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("video_path", "png_folder", "preview_path")
    FUNCTION = "save_v2"
    OUTPUT_NODE = True
    CATEGORY = "Bruxos do VFX/Video"

    @staticmethod
    def _extension(codec: str) -> str:
        return "webm" if codec == "vp9" else ("mov" if codec == "prores" else "mp4")

    @staticmethod
    def _next_counter_v2(out_dir: str, name: str) -> int:
        """Conta masters de qualquer container sem confundir proxy/thumbnail."""
        pattern = re.compile(
            rf"^{re.escape(name)}_(\d+)(?:_preview)?\.(?:mp4|webm|mov|jpg)$",
            re.IGNORECASE,
        )
        png_folder_pattern = re.compile(
            rf"^{re.escape(name)}_(\d+)_pngs$", re.IGNORECASE
        )
        highest = 0
        for filename in os.listdir(out_dir):
            match = pattern.match(filename)
            if match is None:
                match = png_folder_pattern.match(filename)
            if match:
                highest = max(highest, int(match.group(1)))
        return highest + 1

    @staticmethod
    def _ui_ref(path: str, mime: str) -> dict:
        base = (
            _video.folder_paths.get_output_directory()
            if _video._HAS_FP
            else os.path.abspath("output")
        )
        rel_sub = os.path.relpath(os.path.dirname(path), base)
        return {
            "filename": os.path.basename(path),
            "subfolder": "" if rel_sub == "." else rel_sub,
            "type": "output",
            "format": mime,
        }

    @staticmethod
    def _validate_video(path: str) -> None:
        if not os.path.isfile(path) or os.path.getsize(path) < 512:
            raise RuntimeError("arquivo vazio ou incompleto")
        if not _video._HAS_AV:
            return
        container = None
        try:
            container = _video.av.open(path)
            stream = next((s for s in container.streams if s.type == "video"), None)
            if stream is None:
                raise RuntimeError("arquivo sem stream de video")
            frame = next(container.decode(stream), None)
            if frame is None or frame.width <= 0 or frame.height <= 0:
                raise RuntimeError("nao foi possivel decodificar o primeiro frame")
        finally:
            if container is not None:
                container.close()

    @classmethod
    def _encode_atomic(
        cls, frames, final_path: str, fps: float, codec: str, crf: int, pix_fmt: str
    ) -> str:
        stem, ext = os.path.splitext(final_path)
        temp_path = f"{stem}.writing-{uuid.uuid4().hex[:8]}{ext}"
        try:
            is_high_bit = bool(frames) and frames[0].dtype == np.uint16
            if is_high_bit:
                if not _video._HAS_AV:
                    raise RuntimeError("encode 10-bit precisa do PyAV")
                _video._encode_mp4_av(frames, temp_path, fps, codec, crf, pix_fmt)
            elif _video._HAS_IMAGEIO:
                _video._encode_mp4_imageio(frames, temp_path, fps, codec, crf, pix_fmt)
            elif _video._HAS_AV:
                _video._encode_mp4_av(frames, temp_path, fps, codec, crf, pix_fmt)
            else:
                raise RuntimeError("sem backend de encode (imageio-ffmpeg ou PyAV)")
            cls._validate_video(temp_path)
            os.replace(temp_path, final_path)
            return final_path
        except Exception:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass
            raise

    @staticmethod
    def _frame_u8(frame: np.ndarray) -> np.ndarray:
        if frame.dtype == np.uint16:
            return (frame.astype(np.float32) / 257.0).round().astype(np.uint8)
        return frame.astype(np.uint8, copy=False)

    @classmethod
    def _save_thumbnail(cls, frames, path: str) -> str:
        # O frame central costuma representar melhor o resultado que o primeiro.
        frame = cls._frame_u8(frames[len(frames) // 2])
        Image.fromarray(frame[..., :3], mode="RGB").save(path, quality=92)
        return path

    @classmethod
    def _proxy_frames(cls, frames, max_side: int):
        first = cls._frame_u8(frames[0])
        height, width = first.shape[:2]
        scale = min(1.0, float(max_side) / float(max(width, height)))
        out_w = max(2, int(round(width * scale)) // 2 * 2)
        out_h = max(2, int(round(height * scale)) // 2 * 2)
        if out_w == width and out_h == height:
            return [cls._frame_u8(frame)[..., :3] for frame in frames]

        resized = []
        for frame in frames:
            rgb = cls._frame_u8(frame)[..., :3]
            if _video._HAS_CV2:
                item = _video.cv2.resize(
                    rgb, (out_w, out_h), interpolation=_video.cv2.INTER_AREA
                )
            else:
                item = np.asarray(
                    Image.fromarray(rgb, mode="RGB").resize(
                        (out_w, out_h), Image.Resampling.LANCZOS
                    )
                )
            resized.append(np.ascontiguousarray(item))
        return resized

    def save_v2(
        self,
        images,
        filename_prefix,
        fps,
        save_mp4=True,
        codec="h264",
        crf=19,
        pix_fmt="yuv420p",
        save_png_sequence=False,
        png_in_subfolder=True,
        png_prefix="frame",
        date_subfolder=False,
        pingpong=False,
        audio=None,
        bit_depth="8-bit (padrão)",
        preview_mode="proxy H.264 (recomendado)",
        preview_max_side=960,
        preview_crf=30,
    ):
        out_dir, name = self._resolve_outdir(filename_prefix, date_subfolder)
        counter = self._next_counter_v2(out_dir, name)
        is_10bit = str(bit_depth).startswith("10")
        frames = self._frames_arr(images, pingpong, "10-bit" if is_10bit else "8-bit")
        if not frames:
            raise RuntimeError("[Save Video 2.0 Bruxos] nenhum frame recebido")

        video_path = ""
        png_folder = ""
        preview_path = ""

        if save_png_sequence:
            png_folder = (
                os.path.join(out_dir, f"{name}_{counter:05d}_pngs")
                if png_in_subfolder
                else out_dir
            )
            _video._write_png_sequence(frames, png_folder, prefix=png_prefix, start=1)

        enc_codec, enc_pixfmt, warnings = _video._apply_bit_depth(
            codec, pix_fmt, bit_depth
        )
        for warning in warnings:
            print(f"[Save Video 2.0 Bruxos] {warning}", flush=True)

        if save_mp4:
            ext = self._extension(enc_codec)
            video_path = os.path.join(out_dir, f"{name}_{counter:05d}.{ext}")
            self._encode_atomic(
                frames, video_path, float(fps), enc_codec, int(crf), enc_pixfmt
            )
            # O mux ocorre antes da publicacao do preview. Valida novamente porque
            # ffmpeg pode falhar mesmo depois de o stream de video estar correto.
            _video._mux_audio(video_path, audio, fps)
            self._validate_video(video_path)

        thumb_path = os.path.join(out_dir, f"{name}_{counter:05d}_preview.jpg")
        self._save_thumbnail(frames, thumb_path)

        ui = {}
        if preview_mode == "arquivo original" and video_path:
            preview_path = video_path
            ext = os.path.splitext(video_path)[1].lower()
            mime = "video/quicktime" if ext == ".mov" else f"video/{ext[1:]}"
            ui = {"images": [self._ui_ref(preview_path, mime)], "animated": (True,)}
        elif preview_mode == "somente thumbnail" or not save_mp4:
            preview_path = thumb_path
            ui = {"images": [self._ui_ref(preview_path, "image/jpeg")]}
        else:
            preview_path = os.path.join(out_dir, f"{name}_{counter:05d}_preview.mp4")
            proxy_frames = self._proxy_frames(frames, int(preview_max_side))
            self._encode_atomic(
                proxy_frames,
                preview_path,
                float(fps),
                "h264",
                int(preview_crf),
                "yuv420p",
            )
            ui = {
                "images": [self._ui_ref(preview_path, "video/mp4")],
                "animated": (True,),
            }

        logging.info(
            "[Save Video 2.0 Bruxos] master=%s preview=%s png=%s frames=%d",
            video_path or "-",
            preview_path or "-",
            png_folder or "-",
            len(frames),
        )
        return {"ui": ui, "result": (video_path, png_folder, preview_path)}


BruxosSaveVideoV2.DESCRIPTION = (
    "Save Video 2.0 (Bruxos TESTE) — salva o master e usa o preview nativo do "
    "ComfyUI. Por padrao cria um proxy H.264/yuv420p leve apenas para visualizar; "
    "H.265, ProRes, VP9, 10-bit e 4:2:2/4:4:4 continuam intactos no master. "
    "A gravacao usa arquivo temporario, valida o primeiro frame e so entao publica "
    "o resultado, evitando previews de arquivos incompletos."
)


NODE_CLASS_MAPPINGS = {"BruxosSaveVideoV2": BruxosSaveVideoV2}
NODE_DISPLAY_NAME_MAPPINGS = {
    "BruxosSaveVideoV2": "Save Video 2.0 (Bruxos TESTE)"
}
