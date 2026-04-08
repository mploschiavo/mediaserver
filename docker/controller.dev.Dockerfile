# Media Stack Controller — Development Image
# Includes full source, tests, docs, and dev tools.
#
# Build:  docker build -f docker/controller.dev.Dockerfile -t media-stack-controller:dev .
# Usage:  docker run -it media-stack-controller:dev python3 -m pytest tests/

FROM python:3.12-alpine

ARG VERSION=dev
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown

LABEL org.opencontainers.image.title="Media Stack Controller (Dev)" \
      org.opencontainers.image.description="Development image with tests, docs, and dev tools" \
      org.opencontainers.image.version="${VERSION}-dev" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="https://github.com/mploschiavo/mediaserver" \
      org.opencontainers.image.licenses="AGPL-3.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/media-stack/src:/opt/media-stack \
    MEDIA_STACK_VERSION=${VERSION}-dev

WORKDIR /opt/media-stack

RUN pip install --no-cache-dir bcrypt docker kubernetes pyyaml requests

# Full repo copy (production + dev)
COPY VERSION /opt/media-stack/VERSION
COPY bin /opt/media-stack/bin
COPY src /opt/media-stack/src
COPY contracts /opt/media-stack/contracts
COPY config/defaults /opt/media-stack/config/defaults
COPY tests /opt/media-stack/tests
COPY docs /opt/media-stack/docs
COPY examples /opt/media-stack/examples
COPY dist /opt/media-stack/dist
COPY docker /opt/media-stack/docker
COPY k8s /opt/media-stack/k8s

# Dev tools
RUN pip install --no-cache-dir pytest playwright

EXPOSE 9100

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
  CMD wget -qO- http://127.0.0.1:9100/healthz || exit 1
