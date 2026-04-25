# Media Stack Controller — Development Image
#
# ADR-0001 Phase 12-E: production image (controller.Dockerfile) now
# installs from a pre-built wheel and ships zero source. The dev
# image deliberately keeps the source tree mounted under
# /opt/media-stack so operators can `docker run -it … bash` and edit
# in place; the editable install (`pip install -e .`) reflects edits
# without a rebuild. That's the whole point of the dev variant.
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
    MEDIA_STACK_VERSION=${VERSION}-dev

WORKDIR /opt/media-stack

# Same runtime-deps install as production (controller.Dockerfile) so
# the dev image stays a faithful proxy. argon2-cffi + bcrypt require
# a C toolchain on alpine; purge build-deps after install.
RUN apk add --no-cache openssl \
    && apk add --no-cache --virtual .build-deps gcc musl-dev libffi-dev \
    && pip install --no-cache-dir \
        argon2-cffi \
        bcrypt \
        docker \
        jsonschema \
        kubernetes \
        pyyaml \
        requests \
    && apk del .build-deps

# Full repo copy (production + dev). The dev image keeps ``bin/``
# because the shell wrappers (release.sh, regen-dist.sh, etc.) are
# legitimate dev-host tooling and the dev image is where you'd run
# them. The production image at ``controller.Dockerfile`` skips
# ``bin/`` since the runtime never invokes them.
COPY VERSION /opt/media-stack/VERSION
COPY README.md /opt/media-stack/README.md
COPY pyproject.toml /opt/media-stack/pyproject.toml
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

# Editable install so the [project.scripts] entry-points
# (media-stack-controller, etc.) resolve on PATH.
RUN pip install --no-deps --no-cache-dir -e /opt/media-stack

# Dev tools (playwright requires glibc, skip on Alpine — run browser tests on host)
RUN pip install --no-cache-dir pytest

EXPOSE 9100

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
  CMD wget -qO- http://127.0.0.1:9100/healthz || exit 1

# Default: open a shell so devs can ``docker run -it`` and explore.
# To launch the controller, override: ``docker run … media-stack-controller --serve``.
