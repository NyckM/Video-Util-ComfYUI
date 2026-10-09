# -*- coding: utf-8 -*-
r"""
Bruxos do VFX — Hold to Full Seconds
====================================
Completa a sequencia ate o proximo segundo inteiro repetindo (hold) o ultimo frame.

  47f @ 24fps -> 48f (2s), hold de 1f
  51f @ 24fps -> 72f (3s), hold de 21f
  48f @ 24fps -> 48f (2s), sem hold

seconds > 0 fixa a duracao: 47f com seconds=4 -> 96f (hold de 49f).
Se o material for MAIOR que o pedido, if_longer decide: cortar o fim ou dar erro.

Com fps fracionado (23.976, 29.97) o alvo e round(segundos * fps): 2s @ 23.976 = 48f.
"""

import math

try:
    import torch
except Exception:  # pragma: no cover
    torch = None

CAT = "Bruxos do VFX/Video"
TAG = "[Bruxos Hold Seconds]"


IF_LONGER = ["cortar o fim", "erro"]


def target_frames(n, fps, seconds=0):
    """-> (segundos, frames_alvo). seconds=0: menor inteiro de segundos que cabe n frames."""
    if n <= 0:
        raise ValueError(f"{TAG} a entrada nao tem frames.")
    if fps <= 0:
        raise ValueError(f"{TAG} fps precisa ser maior que 0.")
    if int(seconds) > 0:
        return int(seconds), int(round(int(seconds) * fps))
    secs = max(1, math.floor(n / fps))
    while round(secs * fps) < n:
        secs += 1
    return secs, int(round(secs * fps))


def _hold(t, total):
    """Repete o ultimo item do batch ate ter 'total' itens (sem copiar memoria no expand)."""
    n = t.shape[0]
    if total <= n:
        return t
    last = t[-1:].expand(total - n, *t.shape[1:])
    return torch.cat([t, last], dim=0)


class BruxosHoldToSeconds:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE", {"tooltip": "Frames do video ou do EXR."}),
                "fps": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 240.0, "step": 0.001,
                                  "tooltip": "Taxa do material. 23.976 / 29.97 funcionam."}),
            },
            "optional": {
                "mask": ("MASK", {"tooltip": "Opcional: recebe o mesmo hold."}),
                "seconds": ("INT", {"default": 0, "min": 0, "max": 3600,
                    "tooltip": "0 = automatico (arredonda pra cima ate o proximo segundo inteiro). "
                               ">0 = duracao fixa em segundos; completa com hold do ultimo frame."}),
                "if_longer": (IF_LONGER, {"default": "cortar o fim",
                    "tooltip": "So vale com seconds > 0: o que fazer se o material tiver MAIS "
                               "frames que a duracao pedida."}),
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK", "INT", "INT", "FLOAT", "INT", "FLOAT", "STRING")
    RETURN_NAMES = ("images", "mask", "frame_count", "seconds", "duration", "hold_frames", "fps", "info")
    FUNCTION = "run"
    CATEGORY = CAT
    DESCRIPTION = ("Completa ate o proximo segundo inteiro repetindo o ultimo frame (hold). "
                   "47f@24 -> 48f (2s); 51f@24 -> 72f (3s). Ou fixe a duracao em 'seconds'.")

    def run(self, images, fps=24.0, mask=None, seconds=0, if_longer="cortar o fim"):
        n = int(images.shape[0])
        fps = float(fps)
        secs, total = target_frames(n, fps, seconds)
        cut = 0
        if total < n:
            if str(if_longer).startswith("erro"):
                raise ValueError(
                    f"{TAG} o material tem {n}f ({n / fps:.2f}s) e voce pediu {secs}s = {total}f. "
                    f"Aumente seconds, use 0 (automatico) ou mude if_longer para 'cortar o fim'.")
            cut = n - total
            images = images[:total]
            if mask is not None and mask.dim() == 3 and mask.shape[0] > total:
                mask = mask[:total]
            n = total
        hold = total - n

        out = _hold(images, total)

        if mask is None:
            m = torch.zeros((total, images.shape[1], images.shape[2]),
                            dtype=images.dtype, device=images.device)
        else:
            m = mask if mask.dim() == 3 else mask.unsqueeze(0)
            if m.shape[0] == 1 and n > 1:
                m = m.expand(n, *m.shape[1:])   # mascara unica vale pra todos
            elif m.shape[0] < n:
                m = _hold(m, n)                 # mascara mais curta: segura a ultima
            else:
                m = m[:n]
            m = _hold(m, total)

        orig = n + cut
        info = (f"{orig}f @ {fps:g}fps -> {total}f ({secs}s"
                + (", fixo)" if int(seconds) > 0 else ", auto)")
                + (f", hold de {hold}f no frame {n}" if hold else "")
                + (f", cortados {cut}f do fim" if cut else "")
                + ("" if hold or cut else ", sem hold"))
        print(f"{TAG} {info}", flush=True)
        return (out, m, total, secs, total / fps, hold, fps, info)


NODE_CLASS_MAPPINGS = {"BruxosHoldToSeconds": BruxosHoldToSeconds}
NODE_DISPLAY_NAME_MAPPINGS = {"BruxosHoldToSeconds": "Hold to Full Seconds (Bruxos)"}
