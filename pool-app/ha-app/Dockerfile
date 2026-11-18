FROM python:3.12-alpine
ARG BUILD_VERSION=0.9.1
ARG BUILD_ARCH=aarch64
LABEL io.hass.version="${BUILD_VERSION}" io.hass.type="app" io.hass.arch="${BUILD_ARCH}"
RUN apk add --no-cache certbot certbot-dns-cloudflare tzdata
WORKDIR /app
COPY server.py chemistry.py ha_pool.py tls_manager.py ha_run.py chart.json ./
COPY public ./public
# ha_run.py switches on Home Assistant mode itself when the Supervisor started it.
ENV HOST=0.0.0.0 PORT=8745 POOL_DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 8745 8746
CMD ["python", "ha_run.py"]
