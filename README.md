# Video-Util-ComfYUI (Bruxos Media I/O)

**[English](#english) · [Português](#português)**

Media in and out for ComfyUI: Load Image, Load EXR (+OCIO), Load Video, Save Video, SSD frame cache,
deband/anti-banding and an A/B video comparer. Part of the **Bruxos do VFX** family.

Tudo que entra e sai de mídia no ComfyUI: Load Image, Load EXR (+OCIO), Load Video, Save Video, cache de
frames em SSD, deband/anti-banding e comparador A/B de vídeo. Parte da família **Bruxos do VFX**.

---

## English

### Install

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/NyckM/Video-Util-ComfYUI
pip install -r Video-Util-ComfYUI/requirements.txt
```

Then restart ComfyUI. On the Windows portable / Easy-Install build, run pip with the embedded Python:

```bat
..\..\python_embeded\python.exe -m pip install -r Video-Util-ComfYUI\requirements.txt
```

If you already have `opencv-contrib-python` (or another OpenCV build) installed, remove the `opencv-python`
line from `requirements.txt` before installing, so two OpenCV packages do not end up side by side.

**Easy install (Windows):** the [bruxosdovfx SyncTile](https://github.com/NyckM/Bruxosdovfx-SyncTile-MiniMax-H3-v5.4)
repo ships `bruxosdovfx_EasyInstall_MiniMaxH3.bat`, which installs this pack together with SyncTile and the
MiniMax H3 models.

### Dependencies

- `opencv-python` and `imageio[ffmpeg]`: decode/encode. PyAV ships with ComfyUI and is the preferred backend.
- `scenedetect`: cut detection (optional).

### Nodes (17)

| id | menu name | file |
|---|---|---|
| `BruxosLoadVideoPro` | 🧙 Load Video Pro (Bruxos) | `bruxos_load_video_pro.py` |
| `BruxosSaveVideoPro` | 🧙 Save Video Pro (Bruxos) | `bruxos_save_video_pro.py` |
| `BruxosLoadVideo` | Load Video (Bruxos) | `video_nodes.py` |
| `BruxosSaveVideo` | Save Video (Bruxos) | `video_nodes.py` |
| `BruxosLoadImage` | Load Image + Crop (Bruxos) | `bruxos_load_media.py` |
| `BruxosLoadEXR` | Load EXR + OCIO + Crop (Bruxos) | `bruxos_load_exr.py` |
| `BruxosHoldToSeconds` | Hold to Full Seconds (Bruxos) | `bruxos_hold_seconds.py` |
| `BruxosLoadImageV2` | Load Image + Crop 2.0 (Bruxos TESTE) | `bruxos_load_media_v2.py` |
| `BruxosLoadVideoV2` | Load Video 2.0 (Bruxos TESTE) | `bruxos_load_media_v2.py` |
| `BruxosSaveVideoV2` | Save Video 2.0 (Bruxos TESTE) | `bruxos_save_video_v2.py` |
| `BruxosAbrirCacheDisco` | Open Disk Cache (Bruxos) | `bruxos_disk_stream.py` |
| `BruxosCacheParaVideo` | Disk Cache -> Video (Bruxos) | `bruxos_disk_stream.py` |
| `BruxosDiscoLerJanela` | Disk Cache -> Window (Bruxos) | `bruxos_disk_stream.py` |
| `BruxosImagensParaDisco` | Images -> Disk Cache (Bruxos) | `bruxos_disk_stream.py` |
| `BruxosVideoParaDisco` | Video -> Disk Cache (Bruxos) | `bruxos_disk_stream.py` |
| `BruxosDeband` | Deband / Anti-Banding (Bruxos) | `bruxos_deband.py` |
| `BruxosVideoCompare` | Comparar Vídeos A/B (Bruxos) | `video_compare.py` |

The 🧙 Pro nodes, Load Image and Load EXR are the ones used by the bruxosdovfx MiniMax H3 workflows.

### Notes

- This pack registers the HTTP routes `/bruxos/video_thumbnail`, `/bruxos/video_preview` and
  `/bruxos/video_probe`, which feed the Load Video gallery and preview in the front end.
- The **disk frame cache** nodes (`Video -> Disk Cache`, `Disk Window`, etc.) live here. Bernini SSD-81 and
  H3 Contex Loop carry a copy of the same code, so they work without this pack, but the full SSD cache flow is here.
- `bruxos_core/` is the shared Bruxos library (masks, 4n+1, video encode/decode, latents, memory cleanup,
  context windows, Qwen-VL loader). It is copied into this repo on purpose, so the pack installs on its own,
  without depending on install order and without importing folders whose names contain `-`.

### License

Apache-2.0. Third-party credits in `THIRD_PARTY_NOTICES.md`.

---

## Português

### Instalação

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/NyckM/Video-Util-ComfYUI
pip install -r Video-Util-ComfYUI/requirements.txt
```

Depois reinicie o ComfyUI. No ComfyUI portátil / Easy-Install (Windows), use o Python embutido:

```bat
..\..\python_embeded\python.exe -m pip install -r Video-Util-ComfYUI\requirements.txt
```

Se você já tem `opencv-contrib-python` (ou outro OpenCV) instalado, tire a linha `opencv-python` do
`requirements.txt` antes de instalar, para não ficar com dois pacotes de OpenCV juntos.

**Instalação fácil (Windows):** o repositório do [bruxosdovfx SyncTile](https://github.com/NyckM/Bruxosdovfx-SyncTile-MiniMax-H3-v5.4)
traz o `bruxosdovfx_EasyInstall_MiniMaxH3.bat`, que instala este pack junto com o SyncTile e os modelos do MiniMax H3.

### Dependências

- `opencv-python` e `imageio[ffmpeg]`: decode/encode. O PyAV já vem com o ComfyUI e é o backend preferido.
- `scenedetect`: detecção de corte (opcional).

### Nodes

A lista completa (17 nodes) está na tabela da seção em inglês acima. Os nodes 🧙 Pro, o Load Image e o
Load EXR são os usados nos workflows MiniMax H3 da bruxosdovfx.

### Observações

- Este pacote registra as rotas HTTP `/bruxos/video_thumbnail`, `/bruxos/video_preview` e
  `/bruxos/video_probe`, que alimentam a galeria e o preview do Load Video no front.
- Os nodes de **cache de frames em disco** (`Video -> Disk Cache`, `Disk Window` etc.) moram aqui. O Bernini
  SSD-81 e o H3 Contex Loop usam uma cópia do mesmo código, então funcionam sem este pacote, mas o fluxo
  completo de cache em SSD é daqui.
- A pasta `bruxos_core/` é a biblioteca comum dos pacotes Bruxos (máscara, 4n+1, encode/decode de vídeo,
  latentes, limpeza de memória, janelas de contexto, loader do Qwen-VL). Ela vem copiada dentro deste
  repositório de propósito: o pacote instala sozinho, sem depender da ordem de instalação e sem importar
  pastas com `-` no nome.

### Licença

Apache-2.0. Créditos de terceiros em `THIRD_PARTY_NOTICES.md`.
