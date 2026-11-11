# Third-party components

## Used by the core

| Component | Used for | Licence |
|---|---|---|
| NumPy, SciPy | signal processing | BSD-3-Clause |
| python-soundfile / libsndfile | WAV and FLAC I/O | BSD-3-Clause / LGPL-2.1 |
| ONNX Runtime | runs Silero VAD | MIT |
| Silero VAD v5 | speech detection model, downloaded and checksummed on first use | MIT |
| FastAPI, Starlette, Pydantic | HTTP API | MIT |
| Uvicorn | HTTP server | BSD-3-Clause |
| React | web UI | MIT |
| Inter, JetBrains Mono, Noto Sans TC | web UI fonts | SIL OFL 1.1 |
| FFmpeg (Debian package in the images) | decoding, preview encoding | LGPL-2.1+ / GPL-2.0+ |

## Used by Background repair (optional)

Installed only when you choose it, or included in the GPU image. The complete list of pinned packages is in
[openaudiofix/gpu-requirements.txt](openaudiofix/gpu-requirements.txt).

| Component | Used for | Licence |
|---|---|---|
| ClearerVoice-Studio / MossFormer2_SE_48K | background repair model | Apache-2.0 |
| PyTorch | GPU inference | BSD-3-Clause |

## Algorithms

The declipper averages a frame-joint constrained AR solve with A-SPADE (Kitić, Bertin, Gribonval, "Sparsity and
cosparsity for audio declipping", LVA/ICA 2015).
