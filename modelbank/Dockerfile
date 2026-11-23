# Build context = this folder. tools/export_addon.sh copies app/, frontend/ and requirements.txt next to this file.
ARG BUILD_FROM

FROM node:22-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN mkdir -p /build/app/static && npm run build

FROM ${BUILD_FROM}
WORKDIR /opt/modelbank
COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt
COPY app/ ./app/
COPY --from=frontend /build/app/static/dist ./app/static/dist
COPY rootfs/ /
RUN chmod a+x /etc/services.d/modelbank/run /etc/services.d/modelbank/finish

ENV MODELBANK_ADDON=1 \
    MODELBANK_DATA_DIR=/data \
    MODELBANK_MODELS_DIR=/share/modelbank \
    MODELBANK_PORT=8099 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

LABEL io.hass.name="Modelbank" \
      io.hass.description="3D-modeller (STL/3MF) med lager, printhistorik og pris fra FilamentHub" \
      io.hass.type="addon"
