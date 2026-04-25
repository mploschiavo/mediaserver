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
    MEDIA_STACK_VERSION=${VERSION}

WORKDIR /opt/media-stack

RUN apk add --no-cache openssl \
    && apk add --no-cache --virtual .build-deps gcc musl-dev libffi-dev \
    && pip install --no-cache-dir argon2-cffi bcrypt docker kubernetes pyyaml requests \
    && apk del .build-deps

# Install the package so the ``[project.scripts]`` entry-points
# (media-stack-controller, media-stack-generate-envoy-config, …)
# land on /usr/local/bin. ``--no-deps`` because we already
# installed the runtime libs above; pip would otherwise pull
# fresh copies and bloat the image.
COPY VERSION /opt/media-stack/VERSION
COPY pyproject.toml /opt/media-stack/pyproject.toml
COPY src /opt/media-stack/src
COPY config/defaults /opt/media-stack/config/defaults
COPY contracts /opt/media-stack/contracts
RUN pip install --no-deps --no-cache-dir /opt/media-stack

# Shell wrappers under ``bin/`` (release.sh, regen-dist.sh, etc.)
# are dev-host tooling, not runtime. Skip them — the entry-points
# above cover everything the container actually executes.

# Generate bootstrap config JSON from contracts at build time.
# Best-effort: if anything fails, the controller regenerates at
# first boot from the same contracts.
RUN media-stack-generate-bootstrap-config \
        /opt/media-stack/contracts \
        /opt/media-stack/contracts/media-stack.config.json \
        /opt/media-stack/contracts/media-stack.profile.yaml \
    || echo "WARN: Config generation failed (will auto-generate at runtime)"

EXPOSE 9100

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
  CMD wget -qO- http://127.0.0.1:9100/healthz || exit 1

# ENTRYPOINT is the controller console-script. Manifests pass
# `--serve` and the rest as CMD — see k8s/controller.yaml /
# docker-compose.yml. (Phase 12-C: replaces the old
# ``python3 /opt/media-stack/bin/controller.py`` invocation;
# bin/controller.py was a 5-line ``from … import main; main()``
# wrapper around the same entry-point.)
ENTRYPOINT ["media-stack-controller"]
