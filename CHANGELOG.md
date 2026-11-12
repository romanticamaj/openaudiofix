# Changelog

This project follows [Semantic Versioning](https://semver.org/).

## 0.1.1

- The GPU Docker image builds again: the GPU install now works with pip older than 24.1.

## 0.1.0

First public release.

### Repairs
- Declip: rebuilds flattened peaks with a frame-joint AR solve averaged with A-SPADE.
- Glitch repair: fixes buffer-dropout seams with a phase-aligned micro-warp.
- Keep speech only: removes what is not talking and keeps it on a second track.
- Background repair: optional MossFormer2 noise removal on NVIDIA GPUs.

### App
- Web workspace with waveform, spectrogram, fix list and A/B switching on the same word.
- Remaining-time estimate, a completion summary and an export dialog with a folder picker.
- Storage settings, model management and in-app GPU install.
- `afix` command line, Docker images, English and Traditional Chinese UI.
