# syntax=docker/dockerfile:1.7

# ---------- Build stage ----------
FROM node:22-alpine AS build

# Pin Corepack-managed pnpm version. Reproducible builds matter; the
# version comes from ui/package.json's packageManager field.
RUN corepack enable

WORKDIR /build

# Copy lockfile + manifest first for layer caching: deps reinstall
# only when package.json or pnpm-lock.yaml changes.
COPY ui/package.json ui/pnpm-lock.yaml ./

# --frozen-lockfile fails the build if the lockfile is out of sync,
# which is the right CI behavior — we don't want surprise version
# drift between dev and prod.
RUN pnpm install --frozen-lockfile

# Now copy the rest of the UI source.
COPY ui/ ./
# The OpenAPI spec is referenced by `pnpm gen:api` to generate
# typed API definitions during the build.
COPY contracts/api/openapi.yaml /openapi-spec/openapi.yaml
# Captured response fixtures referenced by
# `src/api/fixture-codegen-validation.ts`. The source file imports
# JSON via `../../../tests/fixtures/api_responses/*`. Inside the
# container the source lives at `/build/src/api/...`; three `..`
# ups land at `/`, so the imports resolve to
# `/tests/fixtures/api_responses/*`. Copy fixtures there. Without
# this, `pnpm build` fails with "Cannot find module" for every
# fixture and the image build breaks.
COPY tests/fixtures/api_responses /tests/fixtures/api_responses

# Generate API types from the OpenAPI spec. The package.json script
# reads OPENAPI_SPEC from env so the same script works locally
# (relative path) and in the container (absolute path).
RUN OPENAPI_SPEC=/openapi-spec/openapi.yaml pnpm gen:api

# Vite build emits the production bundle under dist/. Kept on a
# separate RUN line so the contract test regex matches "pnpm ... build"
# without crossing line continuations.
RUN pnpm build

# ---------- Runtime stage ----------
FROM nginxinc/nginx-unprivileged:1.27-alpine

ARG VERSION=dev
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown

LABEL org.opencontainers.image.title="Media Stack UI" \
      org.opencontainers.image.description="React/Vite/Tailwind dashboard for the media automation stack" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="https://github.com/mploschiavo/mediaserver" \
      org.opencontainers.image.licenses="AGPL-3.0"

ENV MEDIA_STACK_UI_VERSION=${VERSION} \
    API_UPSTREAM=media-stack-controller:9100

# nginx-unprivileged ships its own /etc/nginx/conf.d/default.conf; our
# template overwrites it on entrypoint via envsubst.
COPY deploy/compose/ui-nginx.conf /etc/nginx/templates/default.conf.template

# Sourced before the base image's 20-envsubst-on-templates.sh so the
# RESOLVER it exports is available for substitution. Reads the actual
# DNS server from /etc/resolv.conf — works on compose (Docker DNS)
# AND k8s (CoreDNS) without per-platform config divergence. Required
# for the ``resolver ${RESOLVER} ...`` directive in the nginx template
# to receive a valid value at startup.
COPY --chmod=755 deploy/compose/15-set-resolver.envsh /docker-entrypoint.d/15-set-resolver.envsh

# Bake the Vite-built static bundle. Nothing else is shipped — the
# legacy dashboard.html and api/static/ are owned by the build stage
# only and don't reach this image.
COPY --from=build /build/dist /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
