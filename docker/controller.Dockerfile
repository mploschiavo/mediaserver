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

RUN pip install --no-cache-dir bcrypt docker kubernetes pyyaml requests

COPY VERSION /opt/media-stack/VERSION
COPY bin /opt/media-stack/bin
COPY src /opt/media-stack/src
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
