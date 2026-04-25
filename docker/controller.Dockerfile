FROM python:3.12-alpine

ARG VERSION=dev
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown

LABEL org.opencontainers.image.title="Media Stack Controller" \
      org.opencontainers.image.description="Orchestration controller for the media automation stack" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="https://github.com/mploschiavo/mediaserver" \
      org.opencontainers.image.licenses="AGPL-3.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/media-stack/src:/opt/media-stack \
    MEDIA_STACK_VERSION=${VERSION}

WORKDIR /opt/media-stack

RUN apk add --no-cache openssl \
    && apk add --no-cache --virtual .build-deps gcc musl-dev libffi-dev \
    && pip install --no-cache-dir argon2-cffi bcrypt docker kubernetes pyyaml requests \
    && apk del .build-deps

COPY VERSION /opt/media-stack/VERSION
COPY bin /opt/media-stack/bin
COPY src /opt/media-stack/src
# As of v1.0.175 the dashboard HTML, /api/static/* assets and the
# Swagger UI HTML wrapper are owned by a separate UI container. The
# Python source tree still ships the original files for ratchet
# tests + dev workflows, but the runtime image MUST NOT contain
# them — they're a 5000-line attack surface that's no longer
# served. Strip them from the image right after the source COPY so
# nothing reads them at runtime.
RUN rm -rf /opt/media-stack/src/media_stack/api/static \
    /opt/media-stack/src/media_stack/api/dashboard.html
COPY config/defaults /opt/media-stack/config/defaults
COPY contracts /opt/media-stack/contracts

# Generate bootstrap config JSON from contracts at build time
RUN PYTHONPATH=/opt/media-stack/src python3 \
    /opt/media-stack/src/media_stack/cli/commands/generate_bootstrap_config.py \
    /opt/media-stack/contracts \
    /opt/media-stack/contracts/media-stack.config.json \
    /opt/media-stack/contracts/media-stack.profile.yaml \
    || echo "WARN: Config generation failed (will auto-generate at runtime)"

EXPOSE 9100

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
  CMD wget -qO- http://127.0.0.1:9100/healthz || exit 1
