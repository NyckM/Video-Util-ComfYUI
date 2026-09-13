# -*- coding: utf-8 -*-
r"""
Bruxos do VFX — Deband / Anti-Banding
======================================
PRA QUE SERVE
    Remove/disfarca banding (aquelas faixas em degrau em ceus, pele, luzes
    suaves) ANTES de salvar o video. Inspirado no metodo "Bilateral+Dither"
    do ComfyUI_Bit-Depth-Enhancer (subraoul/ComfyUI_Bit-Depth-Enhancer, MIT),
    reimplementado aqui em cima do que o pack ja usa (torch + cv2 opcional)
    em vez de depender de checkpoints externos (as versoes ABCD/deepDeband
    daquele projeto sao redes treinadas com pesos de varios GB no Google
    Drive -- pesado demais e fragil pra depender disso aqui).

    Duas tecnicas, combinaveis:
      1) Alisamento bilateral (edge-aware): suaviza so as areas planas/
         gradientes onde o banding aparece, protegendo contornos/bordas de
         ficarem borrados. Precisa de OpenCV (cv2), que ja e dependencia
         opcional deste pack (usado no Load/Save Video).
      2) Dither TPDF (ruido triangular): a mesma tecnica usada em
         masterizacao de audio/video pra quebrar degraus de quantizacao --
         adiciona um ruido bem fraco (~1 nivel de 8-bit) que o olho le como
         liso em vez de escada. Nao precisa de cv2, sempre disponivel.

    NAO substitui o bit_depth=10-bit do Save Video (Bruxos) -- os dois
    atacam causas diferentes: este node tira o banding que ja esta na
    imagem (vindo da geracao); o 10-bit evita que o PROPRIO encode
    introduza banding novo. Use os dois juntos pro melhor resultado:

        [gerador] -> Deband (Bruxos) -> Save Video (Bruxos, bit_depth=10-bit)
"""
import logging

import torch

log = logging.getLogger(__name__)
CAT = "Bruxos do VFX/Video"

try:
    import cv2
    import numpy as np
    _HAS_CV2 = True
except Exception:  # pragma: no cover
    _HAS_CV2 = False
    try:
        import numpy as np
    except Exception:
        np = None


def _smooth_frame(frame_u8, sigma, preserve_edges, blend):
    """Alisa UM frame [H,W,3] uint8 e devolve o blend ja pronto (float32 0..1).
    So processa um frame por vez de proposito -- ver comentario em run()."""
    smooth_u8 = cv2.bilateralFilter(frame_u8, 7, sigma, sigma)
    if preserve_edges:
        gray = cv2.cvtColor(frame_u8, cv2.COLOR_RGB2GRAY)
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        mag = np.sqrt(gx * gx + gy * gy)
        ref = float(np.percentile(mag, 99)) or 1.0
        edge = np.clip(mag / ref, 0.0, 1.0)[..., None]  # 1 = borda forte, protegida
        blend_map = blend * (1.0 - edge)
    else:
        blend_map = blend
    frame_f = frame_u8.astype(np.float32) / 255.0
    smooth_f = smooth_u8.astype(np.float32) / 255.0
    return frame_f * (1.0 - blend_map) + smooth_f * blend_map


class BruxosDeband:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE", {"tooltip": "Frames com banding a corrigir."}),
                "strength": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1.0, "step": 0.01, "tooltip":
                    "Intensidade do alisamento bilateral (apaga os degraus). 0 = so o dither (abaixo). "
                    "Precisa de OpenCV -- sem ele, este parametro e ignorado e so o dither roda."}),
                "dither": ("BOOLEAN", {"default": True, "tooltip":
                    "Ruido TPDF (triangular) bem fraco (~1 nivel de 8-bit) somado na imagem -- quebra "
                    "os degraus de quantizacao remanescentes em algo que o olho le como liso. Tecnica "
                    "padrao de masterizacao, nao precisa de cv2, praticamente imperceptivel a olho nu."}),
                "preserve_edges": ("BOOLEAN", {"default": True, "tooltip":
                    "So alisa areas planas/gradientes; protege contornos, texto e regioes de alto "
                    "contraste do borramento. Desligue so se quiser um efeito mais forte/generico."}),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    FUNCTION = "run"
    CATEGORY = CAT
    DESCRIPTION = (
        "Deband / Anti-Banding (Bruxos): alisamento bilateral edge-aware + dither TPDF pra tirar o "
        "banding que ja veio na geracao (ceus, pele, gradientes suaves). Combine com o bit_depth=10-bit "
        "do Save Video (Bruxos) pro melhor resultado -- este node tira o banding existente, o 10-bit "
        "evita que o encode introduza banding novo."
    )

    def run(self, images, strength=0.5, dither=True, preserve_edges=True):
        x = images.detach().float().clamp(0, 1)
        device = x.device
        T = int(x.shape[0])

        do_smooth = strength > 0
        if do_smooth and not _HAS_CV2:
            print("[Bruxos Deband] strength>0 pede OpenCV (cv2) pro alisamento bilateral; "
                  "nao encontrei cv2 -- aplicando so o dither.", flush=True)
            do_smooth = False

        if not do_smooth and not dither:
            return (x.contiguous(),)

        # QUADRO A QUADRO de proposito. A 1a versao deste node processava o
        # lote inteiro de uma vez em numpy (arr_f, smooth_f, blend_map, out...),
        # e cada copia extra do lote em float32 custa T*H*W*3*4 bytes -- num
        # lote de 141 frames em 2048x4096 sao ~13GB POR COPIA, e a conta velha
        # fazia 4-5 copias simultaneas: estourava a RAM (OOM) mesmo com bastante
        # RAM livre, ainda mais com modelos grandes staged em RAM ao mesmo
        # tempo. Processando um frame por vez, o pico de memoria por iteracao
        # e de poucas dezenas de MB, nao importa quantos frames tenham.
        sigma = 10.0 + 50.0 * float(strength)
        blend = float(strength)
        amount = 1.0 / 255.0  # ~1 LSB de 8-bit -- suficiente pra quebrar o degrau, imperceptivel
        out = torch.empty_like(x)

        for i in range(T):
            frame_t = x[i]
            if do_smooth:
                frame_u8 = (frame_t.cpu().numpy() * 255.0).round().astype(np.uint8)
                blended = _smooth_frame(frame_u8, sigma, preserve_edges, blend)
                frame_t = torch.from_numpy(blended.astype(np.float32)).to(device=device)
            if dither:
                noise = (torch.rand_like(frame_t) - torch.rand_like(frame_t)) * amount  # TPDF (triangular)
                frame_t = (frame_t + noise).clamp(0, 1)
            out[i] = frame_t

        return (out.contiguous(),)


NODE_CLASS_MAPPINGS = {"BruxosDeband": BruxosDeband}
NODE_DISPLAY_NAME_MAPPINGS = {"BruxosDeband": "Deband / Anti-Banding (Bruxos)"}
