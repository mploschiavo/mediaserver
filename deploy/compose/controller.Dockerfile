# Media Stack Controller — Production Image (multi-stage, wheel-based)
#
# ADR-0001 Phase 12-E: the runtime stage installs from a pre-built
# wheel, not from the source tree. Phase 12-C made the ENTRYPOINT
# stop reading the source tree (the console-script runs out of
# /usr/local/bin); installing from a wheel finishes the cutover by
# eliminating the source tree from the runtime image entirely.
#
# Why:
#   * Smaller runtime image — no .py sources, no __pycache__, no
#     hatchling build metadata, just the wheel content.
#   * Reproducible builds — wheel hash → image hash. Two builds of
#     the same source produce byte-identical wheels under
#     SOURCE_DATE_EPOCH; the image differs only in build labels.
#   * Canonical Python deployment shape — `pip install <wheel>` is
#     how every other Python service ships.
#
# The wheel itself bundles src/media_stack/**/*.{py,yaml,yml,json}
# (see [tool.hatch.build.targets.wheel] in pyproject.toml) plus the
# contracts/ tree as shared-data under share/media-stack/contracts.
# The runtime stage adds top-level contracts/, config/defaults/, and
# VERSION because controller code reads them at well-known relative
# paths (resolved against /opt/media-stack at WORKDIR).

# ---------------------------------------------------------------------------
# Stage 1: builder — produces a wheel from the source tree.
# ---------------------------------------------------------------------------
FROM python:3.12-alpine AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# `python -m build --wheel` is the PEP 517 canonical builder. It
# isolates the wheel build in a venv so hatchling and its deps don't
# leak into the builder layer (and certainly not into runtime).
RUN pip install --no-cache-dir build==1.2.2.post1

# Copy ONLY what hatchling needs to produce the wheel:
#   * pyproject.toml — build config + project metadata.
#   * README.md — referenced by [project] readme = "README.md".
#   * VERSION — read by src/media_stack/version.py at build time
#     (single-source-of-truth for the package version).
#   * src/ — the package itself.
#   * contracts/ — shared-data target ("contracts" → share/media-stack/contracts).
COPY pyproject.toml /build/pyproject.toml
COPY README.md /build/README.md
COPY VERSION /build/VERSION
COPY src /build/src
COPY contracts /build/contracts

# Build the wheel into /wheels/. --no-isolation would speed this up
# but defeats the "wheel build is hermetic" property; keep isolation.
RUN python -m build --wheel --outdir /wheels /build \
    && ls -la /wheels/

# ---------------------------------------------------------------------------
# Stage 2: runtime — installs the wheel + bundles operator data.
# ---------------------------------------------------------------------------
FROM python:3.12-alpine AS runtime

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
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MEDIA_STACK_VERSION=${VERSION}

WORKDIR /opt/media-stack

# openssl is needed at runtime for TLS-cert generation flows; the
# build-deps trio (gcc/musl-dev/libffi-dev) is needed only to
# compile argon2-cffi/bcrypt wheels on alpine and is purged after
# pip install.
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

# Install the wheel itself. --no-deps because runtime deps were
# installed above; pip would otherwise re-fetch them from PyPI.
# The wheel is named media_stack-<version>-py3-none-any.whl; the
# glob handles the version embedded in the filename.
COPY --from=builder /wheels/*.whl /tmp/
RUN pip install --no-deps --no-cache-dir /tmp/media_stack-*.whl \
    && rm -rf /tmp/media_stack-*.whl

# Operator-readable data the controller resolves against
# /opt/media-stack at runtime. The wheel bundles the same contracts
# tree as shared-data, but several runtime code paths walk
# `<repo>/contracts/...` directly (relative to MEDIA_STACK_REPO_ROOT
# / WORKDIR), so we keep a top-level copy too.
COPY VERSION /opt/media-stack/VERSION
COPY contracts /opt/media-stack/contracts
COPY config/defaults /opt/media-stack/config/defaults

# Generate bootstrap config JSON from contracts at build time.
# Best-effort: if anything fails, the controller regenerates at
# first boot from the same contracts. The console-script lives on
# PATH after the wheel install above.
RUN media-stack-generate-bootstrap-config \
        /opt/media-stack/contracts \
        /opt/media-stack/contracts/media-stack.config.json \
        /opt/media-stack/contracts/media-stack.profile.yaml \
    || echo "WARN: Config generation failed (will auto-generate at runtime)"

EXPOSE 9100

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
  CMD wget -qO- http://127.0.0.1:9100/healthz || exit 1

# ENTRYPOINT is the controller console-script installed by the
# wheel. Manifests pass `--serve` and the rest as CMD — see
# k8s/controller.yaml / docker-compose.yml.
ENTRYPOINT ["media-stack-controller"]
