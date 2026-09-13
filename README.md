# ComfyUI-Bruxos-MediaIO

> Tudo que entra e sai de midia: Load Image, Load Video, Save Video, cache de frames em SSD, deband/anti-banding e comparador A/B de video.

Parte da familia **Bruxos do VFX** para ComfyUI. Este repositorio nasceu da
divisao do pacote unico `ComfyUI-Bruxos-do-VFX`, que ficou grande demais para
instalar e manter inteiro. Cada area agora tem repositorio, issues e instalacao
proprios — voce instala so o que usa.

## Instalacao

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/SEU-USUARIO/ComfyUI-Bruxos-MediaIO
pip install -r ComfyUI-Bruxos-MediaIO/requirements.txt
```

Depois reinicie o ComfyUI.

### Dependencias

- `opencv-python` e `imageio[ffmpeg]` — decode/encode. O PyAV ja vem com o ComfyUI e e o backend preferido.
- `scenedetect` — deteccao de corte (opcional).


## Nodes (13)

| id | nome no menu | arquivo |
|---|---|---|
| `BruxosLoadVideo` | Load Video (Bruxos) | `video_nodes.py` |
| `BruxosSaveVideo` | Save Video (Bruxos) | `video_nodes.py` |
| `BruxosLoadImage` | Load Image + Crop (Bruxos) | `bruxos_load_media.py` |
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
## Observacoes da separacao

- Este pacote registra as rotas HTTP `/bruxos/video_thumbnail`, `/bruxos/video_preview` e
  `/bruxos/video_probe`, que alimentam a galeria e o preview do Load Video no front.
- Os nodes de **cache de frames em disco** (`Video -> Disk Cache`, `Disk Window`, etc.) moram aqui.
  O Bernini SSD-81 e o H3 Contex Loop usam a mesma implementacao por copia, entao funcionam
  mesmo sem este pacote — mas o fluxo completo de cache em SSD e daqui.


## Sobre o `bruxos_core`

A pasta `bruxos_core/` e a biblioteca comum dos pacotes Bruxos (mascara, 4n+1,
encode/decode de video, latentes, limpeza de memoria, janelas de contexto,
loader do Qwen-VL). Ela vem **copiada dentro deste repositorio de proposito**:
assim o pacote instala sozinho, sem depender da ordem de instalacao dos outros e
sem precisar importar pastas com `-` no nome (o Python nao aceita).

Fonte canonica: [SEU-USUARIO/ComfyUI-Bruxos-Core](https://github.com/SEU-USUARIO/ComfyUI-Bruxos-Core).
Quando atualizar a lib la, copie a pasta por cima aqui.

## Licenca

Apache-2.0. Creditos de terceiros em `THIRD_PARTY_NOTICES.md`.
