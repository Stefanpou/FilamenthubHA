# Build context = this folder (addon/filamenthub/). tools/export_addon.sh copies app/, frontend/ and
# requirements.txt next to this file.

ARG BUILD_FROM

# ---- Stage 1: build the Svelte frontend (vite outDir is ../app/static/dist) ----
FROM node:22-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN mkdir -p /build/app/static && npm run build

# ---- Stage 2: runtime ----
FROM ${BUILD_FROM}

WORKDIR /opt/filamenthub

COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY --from=frontend /build/app/static/dist ./app/static/dist
COPY rootfs/ /

RUN chmod a+x /etc/services.d/filamenthub/run /etc/services.d/filamenthub/finish

ENV FILAMENTHUB_ADDON=1 \
    SPOOLSYNC_DATA_DIR=/data \
    SPOOLSYNC_HOST=0.0.0.0 \
    SPOOLSYNC_PORT=8080 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

LABEL io.hass.name="FilamentHub" \
      io.hass.description="Filament-lager og Bambu Lab printer-dashboard" \
      io.hass.type="addon" \
      io.hass.version="0.1.0"
