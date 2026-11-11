# OpenAudioFix: web UI + API + CPU worker, and in the gpu target also the GPU worker (ADR-0012).
#   docker compose up -d                                                   -> target core (CPU modules)
#   docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d   -> target gpu (+ background repair)
# Pins (numpy 1.26.4, scipy 1.15.3, soundfile 0.13.1) come from pyproject.toml and have cp311 wheels.
FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.11-slim-bookworm AS base
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libsndfile1 \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml /app/pyproject.toml
COPY README.md /app/README.md
COPY openaudiofix/ /app/openaudiofix/
COPY server/ /app/server/
RUN pip install --no-cache-dir "/app[server]"
COPY --from=web /web/dist /app/web/dist
# Inside the container the server listens on all interfaces; compose publishes it on 127.0.0.1 only.
# AFIX_GPU_INSTALL=0: the GPU stack is part of the image (gpu) or absent (core); the UI never runs pip here.
ENV AFIX_DATA=/data AFIX_MODELS=/data/models AFIX_HOST=0.0.0.0 AFIX_PORT=8765 AFIX_WEB_DIST=/app/web/dist \
    AFIX_GPU_INSTALL=0 PYTHONUNBUFFERED=1 PYTHONUTF8=1
VOLUME ["/data"]
EXPOSE 8765
CMD ["python", "-m", "server.launcher"]

FROM base AS gpu
# The same install `afix gpu install` does natively: the lock under the core's pins, then ClearVoice alone.
# nvidia-smi and the driver come from the host through the NVIDIA Container Toolkit (compose gpu override).
RUN python -c "from openaudiofix import gpu; gpu.install()" \
 && pip cache purge
# MossFormer2 weights baked in, outside the data volume
ENV MODEL_DIR=/opt/models
RUN python -m openaudiofix.modules.denoise --download

FROM base AS core
