# -*- coding: utf-8 -*-
"""ComfyUI-Bruxos-MediaIO

Load Image, Load EXR (+OCIO), Load Video, Save Video, cache de frames em SSD, deband e comparador A/B de video. Tudo que entra e sai de midia.

Separado do pacote unico ComfyUI-Bruxos-do-VFX.
"""

TAG = "Bruxos — Media I/O (imagem e video)"

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}


def _merge(modname):
    try:
        mod = __import__(f"{__name__}.{modname}", fromlist=["*"])
        NODE_CLASS_MAPPINGS.update(getattr(mod, "NODE_CLASS_MAPPINGS", {}))
        NODE_DISPLAY_NAME_MAPPINGS.update(getattr(mod, "NODE_DISPLAY_NAME_MAPPINGS", {}))
    except Exception as e:  # pragma: no cover
        import logging
        logging.warning(f"[{TAG}] modulo '{modname}' nao carregou: {e}")


_merge("video_nodes")
_merge("bruxos_load_media")
_merge("bruxos_load_media_v2")
_merge("bruxos_load_exr")
_merge("bruxos_hold_seconds")
_merge("bruxos_save_video_v2")
_merge("bruxos_disk_stream")
_merge("bruxos_deband")
_merge("video_compare")


# ---- rotas HTTP de miniatura / preview / probe de video ----
try:
    from server import PromptServer
    from aiohttp import web
    # ---- miniatura do primeiro frame para a galeria do Load Video ---------
    @PromptServer.instance.routes.get("/bruxos/video_thumbnail")
    async def _bruxos_video_thumbnail(request):  # pragma: no cover
        import os, asyncio, hashlib
        import folder_paths

        relative = str(request.query.get("filename", "")).replace("\\", "/").lstrip("/")
        base = os.path.abspath(folder_paths.get_input_directory())
        path = os.path.abspath(os.path.join(base, relative))
        try:
            inside_input = os.path.commonpath([base, path]) == base
        except ValueError:
            inside_input = False
        if not inside_input or not os.path.isfile(path):
            return web.json_response({"error": "arquivo invalido"}, status=400)

        mtime = os.path.getmtime(path)
        key = hashlib.md5(f"{path}|{mtime}|first-frame-v1".encode()).hexdigest()[:20]
        thumb_dir = os.path.join(folder_paths.get_temp_directory(), "bruxos_video_thumbs")
        os.makedirs(thumb_dir, exist_ok=True)
        output = os.path.join(thumb_dir, f"{key}.jpg")

        if not os.path.isfile(output):
            def _make_thumbnail():
                import cv2
                capture = cv2.VideoCapture(path)
                frame = None
                # Alguns containers entregam um primeiro pacote vazio; tente
                # poucos frames sem transformar a miniatura num decode longo.
                for _ in range(8):
                    ok, candidate = capture.read()
                    if ok and candidate is not None and candidate.size:
                        frame = candidate
                        break
                capture.release()
                if frame is None:
                    return False
                height, width = frame.shape[:2]
                scale = min(1.0, 320.0 / max(width, height))
                if scale < 1.0:
                    frame = cv2.resize(
                        frame,
                        (max(2, round(width * scale)), max(2, round(height * scale))),
                        interpolation=cv2.INTER_AREA,
                    )
                return bool(cv2.imwrite(output, frame, [cv2.IMWRITE_JPEG_QUALITY, 82]))

            if not await asyncio.to_thread(_make_thumbnail):
                return web.json_response({"error": "frame indisponivel"}, status=415)

        response = web.FileResponse(output)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    # ---- preview JA CORTADO (estilo VHS advanced): re-renderiza o trecho ----
    @PromptServer.instance.routes.get("/bruxos/video_preview")
    async def _bruxos_video_preview(request):  # pragma: no cover
        import os, asyncio, hashlib
        import folder_paths
        q = request.query
        filename = q.get("filename", "")
        ftype = q.get("type", "input")
        subfolder = q.get("subfolder", "")
        def _i(k, d=0):
            try: return int(float(q.get(k, d)))
            except Exception: return d
        def _f(k, d=0.0):
            try: return float(q.get(k, d))
            except Exception: return d
        skip = max(0, _i("skip_first_frames", 0))
        cap = max(0, _i("frame_load_cap", 0))
        nth = max(1, _i("select_every_nth", 1))
        rate = max(0.0, _f("force_rate", 0.0))
        maxside = max(64, _i("maxside", 720))
        try:
            if ftype == "output":
                base = folder_paths.get_output_directory()
            elif ftype == "temp":
                base = folder_paths.get_temp_directory()
            else:
                base = folder_paths.get_input_directory()
            path = os.path.abspath(os.path.join(base, subfolder, filename))
            if not path.startswith(os.path.abspath(base)) or not os.path.isfile(path):
                return web.json_response({"error": "arquivo invalido"}, status=400)

            mtime = os.path.getmtime(path)
            key = hashlib.md5(f"{path}|{mtime}|{skip}|{cap}|{nth}|{rate}|{maxside}".encode()).hexdigest()[:16]
            tmp = folder_paths.get_temp_directory()
            os.makedirs(tmp, exist_ok=True)
            out = os.path.join(tmp, f"bruxos_prev_{key}.mp4")

            if not os.path.isfile(out):
                def _render():
                    import cv2, numpy as np
                    cap_cv = cv2.VideoCapture(path)
                    src_fps = float(cap_cv.get(cv2.CAP_PROP_FPS)) or 24.0
                    frames = []
                    idx = -1; kept = 0; next_tick = 0.0; step = None
                    HARD = 900  # limite de frames do preview
                    while True:
                        ok, raw = cap_cv.read()
                        if not ok: break
                        idx += 1
                        if idx < skip: continue
                        j = idx - skip
                        if rate and src_fps:
                            if step is None: step = src_fps / rate
                            if j < next_tick - 1e-9: continue
                            next_tick += step
                        else:
                            if j % nth != 0: continue
                        h, w = raw.shape[:2]
                        sc = maxside / max(h, w)
                        if sc < 1.0:
                            raw = cv2.resize(raw, (max(2, int(w*sc)), max(2, int(h*sc))), interpolation=cv2.INTER_AREA)
                        # par (yuv420p exige dimensoes pares)
                        hh, ww = raw.shape[:2]
                        if hh % 2 or ww % 2:
                            raw = raw[:hh - (hh % 2), :ww - (ww % 2)]
                        frames.append(raw)
                        kept += 1
                        if cap and kept >= cap: break
                        if kept >= HARD: break
                    cap_cv.release()
                    if not frames: return False
                    out_fps = rate if rate else (src_fps / nth if src_fps else 24.0)
                    out_fps = max(1.0, out_fps)
                    hh, ww = frames[0].shape[:2]
                    try:
                        import imageio
                        with imageio.get_writer(out, fps=out_fps, codec="libx264",
                                                quality=8, macro_block_size=None,
                                                pixelformat="yuv420p") as wr:
                            for fr in frames:
                                wr.append_data(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))
                        return True
                    except Exception:
                        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                        vw = cv2.VideoWriter(out, fourcc, out_fps, (ww, hh))
                        for fr in frames: vw.write(fr)
                        vw.release()
                        return os.path.isfile(out)
                ok = await asyncio.get_event_loop().run_in_executor(None, _render)
                if not ok or not os.path.isfile(out):
                    return web.json_response({"error": "sem frames apos corte"}, status=400)

            return web.FileResponse(out, headers={"Content-Type": "video/mp4",
                                                   "Cache-Control": "no-cache"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    @PromptServer.instance.routes.get("/bruxos/video_probe")
    async def _bruxos_video_probe(request):  # pragma: no cover
        """Le frames/resolucao/fps/duracao de um video do diretorio input/output/temp,
        pra preencher as infos no node Load Video assim que o video e escolhido."""
        import os
        import folder_paths
        filename = request.query.get("filename", "")
        ftype = request.query.get("type", "input")
        subfolder = request.query.get("subfolder", "")
        try:
            if ftype == "output":
                base = folder_paths.get_output_directory()
            elif ftype == "temp":
                base = folder_paths.get_temp_directory()
            else:
                base = folder_paths.get_input_directory()
            path = os.path.abspath(os.path.join(base, subfolder, filename))
            if not path.startswith(os.path.abspath(base)) or not os.path.isfile(path):
                return web.json_response({"error": "arquivo invalido"}, status=400)
            w = h = fc = 0
            fps = 0.0
            try:
                import cv2
                cap = cv2.VideoCapture(path)
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                fps = float(cap.get(cv2.CAP_PROP_FPS)) or 0.0
                fc = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                cap.release()
            except Exception:
                pass
            dur = (fc / fps) if fps else 0.0

            # --- calcula o recorte (skip / nth / cap / force_rate) igual ao decode_video ---
            def _int(q, d=0):
                try:
                    return int(float(request.query.get(q, d)))
                except Exception:
                    return d
            def _float(q, d=0.0):
                try:
                    return float(request.query.get(q, d))
                except Exception:
                    return d
            skip = max(0, _int("skip_first_frames", 0))
            nth = max(1, _int("select_every_nth", 1))
            cap = max(0, _int("frame_load_cap", 0))
            frate = max(0.0, _float("force_rate", 0.0))

            avail = max(0, fc - skip)
            if frate and fps:
                avail = int(round(avail * (frate / fps)))
            kept = 0 if avail <= 0 else ((avail - 1) // nth + 1)
            if cap:
                kept = min(kept, cap)
            out_fps = frate if frate else (fps / nth if fps else 0.0)
            trim_dur = (kept / out_fps) if out_fps else 0.0

            return web.json_response({
                "width": w, "height": h, "fps": round(fps, 4),
                "frame_count": fc, "duration": round(dur, 4),
                "trim_frames": int(kept), "trim_fps": round(out_fps, 4),
                "trim_duration": round(trim_dur, 4),
                "skip_first_frames": skip, "select_every_nth": nth,
                "frame_load_cap": cap,
                "start_time": round((skip / fps) if fps else 0.0, 4),
                # fracoes (0..1) do video: robustas a divergencia de fps no navegador
                "start_frac": round((skip / fc) if fc else 0.0, 6),
                "end_frac": round(min(1.0, (skip + kept * nth) / fc) if fc else 1.0, 6),
                # span de tempo do arquivo original consumido (p/ o preview parar no cap)
                "end_time": round(((skip + (kept * nth if not frate else avail * nth)) / fps) if fps else 0.0, 4),
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
except Exception as e:  # pragma: no cover
    import logging
    logging.info(f"[{TAG}] rotas de video nao registradas (ok fora do server): {e}")


# Banner de inicializacao
try:
    from .banner import print_banner
    print_banner(area="Media I/O", node_count=len(NODE_CLASS_MAPPINGS), version="v2")
except Exception:
    pass

WEB_DIRECTORY = "./web"
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
