# UI Container Runbook

The `media-stack-ui` container is an nginx process that serves the React 19
SPA bundle (built with Vite 6 + Tailwind v4 + shadcn/ui — see
[ui-design-system.md](../reference/ui-design-system.md)) and reverse-proxies `/api/*` to
the controller container. It replaces the static-serving role the Python
controller used to perform.

As of UI v1.1.0 the legacy `dashboard.html` + `api/static/*` Preact stub is
gone; only the Vite-built `dist/` is shipped in the runtime image.

## Why the split

- **UI iteration speed.** Front-end changes (HTML, CSS, JS) ship as a new UI
  image without rebuilding the API. Rollouts are decoupled.
- **API container shrinks.** The controller no longer carries `dashboard.html`
  or `/static/*`; its image footprint and attack surface drop.
- **Independent versioning.** `VERSION-UI` advances on dashboard changes;
  `VERSION` advances on controller changes. Operators can pin them
  independently.
- **Production-grade headers and caching.** Security headers, gzip, and cache
  policy now live in nginx where they belong, not in Python middleware.

## Architecture

```
                  Browser
                     |
                     v
              Envoy (path-prefix routing)
                     |
                     v
         +--- nginx (UI container) ---- port 8080 -------+
         |                                               |
         |  /assets/*  -> /usr/share/nginx/html/assets   |
         |               (Vite hashed, 1y immutable)     |
         |  /api/*     -> proxy_pass to API service      |
         |  /healthz   -> 200 {"status":"ok"} (kubelet)  |
         |  / + others -> /usr/share/nginx/html/         |
         |                index.html (SPA fallback,      |
         |                Cache-Control: no-cache)       |
         +-----------------------------------------------+
                     |
                     v   (proxy /api/* over cluster network)
              API container (Python, port 9100)
              - K8s: Service "media-stack-controller"
              - Compose: service "bootstrap"
```

## Build and push

The build is a thin bash wrapper over a Python CLI, identical in shape to
`bin/build-controller-image.sh`:

```
bin/build-ui-image.sh                  # builds and pushes
bin/build-ui-image.sh --no-push        # local build only
PUSH_IMAGE=0 bin/build-ui-image.sh     # env-var equivalent
bin/build-ui-image.sh --engine podman  # force podman over docker
```

Defaults:
- Image: `harbor.iomio.io/public/media-stack-ui:v${VERSION-UI}` — current
  shipped tag is `v1.1.0`.
- Dockerfile: [`deploy/compose/ui.Dockerfile`](../../deploy/compose/ui.Dockerfile) — multi-stage
  `node:22-alpine` (pnpm + Vite build) → `nginxinc/nginx-unprivileged:1.27-alpine`.
- Engine: auto-detect (docker preferred, podman fallback)

Override the image ref entirely with `BOOTSTRAP_UI_IMAGE=...` or `--image ...`.

## Deploy

Kubernetes (rolling update):

```
kubectl set image deploy/media-stack-ui \
    ui=harbor.iomio.io/public/media-stack-ui:v1.1.0
kubectl rollout status deploy/media-stack-ui --timeout=120s
```

Compose:

```
docker compose pull ui
docker compose up -d ui
```

## Auto-heal

The image declares a `HEALTHCHECK` against `/healthz` (10s interval, 5s
timeout, 3 retries, 5s start period). Combined with k8s `livenessProbe` and
`readinessProbe` on the same path, a stuck nginx is restarted by the kubelet
and removed from Service endpoints during the failure window. Compose users
get the same effect via `restart: unless-stopped` and a depends-on healthcheck.

Recommended k8s probe block:

```
livenessProbe:
  httpGet:  { path: /healthz, port: 8080 }
  initialDelaySeconds: 5
  periodSeconds: 10
readinessProbe:
  httpGet:  { path: /healthz, port: 8080 }
  initialDelaySeconds: 2
  periodSeconds: 5
```

## Configuration

| Variable        | Default                          | Purpose                                                    |
|-----------------|----------------------------------|------------------------------------------------------------|
| `API_UPSTREAM`  | `media-stack-controller:9100`    | Host:port the `/api/*` reverse proxy targets.              |

`API_UPSTREAM` is substituted into `/etc/nginx/conf.d/default.conf` at
container start by the upstream nginx:alpine envsubst entrypoint, which
processes everything under `/etc/nginx/templates/` -> `/etc/nginx/conf.d/`.
The image must NOT bake this in; resolve at run time only.

Compose example:

```
services:
  ui:
    image: harbor.iomio.io/public/media-stack-ui:v1.1.0
    environment:
      API_UPSTREAM: bootstrap:9100
    ports:
      - "8080:8080"
```

K8s defaults already match the Service name, so no override is required when
the controller Service is named `media-stack-controller`.

## Troubleshooting

- **502 Bad Gateway from /api/...** The API container is unreachable.
  - `kubectl get pods -l app=media-stack-controller` -- are any Ready?
  - `kubectl logs deploy/media-stack-controller --tail=200`
  - Compose: `docker compose ps bootstrap` and `docker compose logs bootstrap`.
- **404 on a hashed `/assets/*.js`.** The browser is holding a stale
  `index.html` referencing a hash that doesn't exist in the new image.
  Confirm `kubectl exec deploy/media-stack-ui -- ls /usr/share/nginx/html/assets`
  matches the script tags in
  `kubectl exec deploy/media-stack-ui -- cat /usr/share/nginx/html/index.html`.
  `Cache-Control: no-cache` on `/` should make this self-heal on reload.
- **PWA icon 404 / "icon size mismatch" in DevTools.** The PNGs under
  `/usr/share/nginx/html/icons/` must match the `sizes` declared in
  `dist/manifest.webmanifest`. The CI manifest-contract check covers this;
  see [ui-design-system.md](../reference/ui-design-system.md) for the regen step.
- **CSP violation in browser console.** Tighten or relax the policy in
  [`deploy/compose/ui-nginx.conf`](../../deploy/compose/ui-nginx.conf) and rebuild. The current
  policy intentionally allows `https://cdn.jsdelivr.net` for Geist Variable
  font CSS + woff2 only; new third-party origins MUST be added to this doc
  before the conf changes.
- **Healthcheck flapping.** Run `wget -qO- http://127.0.0.1:8080/healthz`
  inside the container; if that works, the kubelet probe is misconfigured
  (port mismatch is the usual cause -- the container listens on 8080).
- **Headers missing on errors.** `add_header ... always` is in use, but
  nginx still strips headers on a few internal responses; cross-check with
  `curl -i http://localhost:8080/healthz`.

## Security

The CSP and supporting headers are emitted by nginx (see
[`deploy/compose/ui-nginx.conf`](../../deploy/compose/ui-nginx.conf)):

- `Content-Security-Policy`:
  ```
  default-src 'self';
  script-src 'self' 'unsafe-inline';
  style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net;
  font-src 'self' data: https://cdn.jsdelivr.net;
  img-src 'self' data: https:;
  connect-src 'self';
  frame-ancestors 'none';
  base-uri 'self';
  form-action 'self';
  object-src 'none'
  ```
  `style-src 'unsafe-inline'` is required for Tailwind v4's runtime style
  injection and Vaul/Sonner positioning; `cdn.jsdelivr.net` is whitelisted for
  Geist Variable only and is fetched as `CacheFirst` by the service worker
  ([Workbox](../../ui/vite.config.ts) config). `script-src 'unsafe-inline'`
  remains for shadcn's small inline bootstrap and `next-themes`'
  flash-of-unstyled-content guard; React 19 + Vite-built scripts themselves
  are all `'self'`.
- `X-Frame-Options: DENY`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: no-referrer`
- `Permissions-Policy: geolocation=(), microphone=(), camera=()`

The container runs as the unprivileged `nginx` user (image default) and
listens on `:8080`, never `:80`, so it never needs CAP_NET_BIND_SERVICE.

TLS terminates at Envoy in front of the UI; nginx itself speaks plain HTTP
on the cluster network. `X-Forwarded-Proto` from Envoy is propagated to the
upstream API on `/api/*` so the controller can honour HTTPS-only logic.

## Caching

- `/assets/*`: `Cache-Control: public, max-age=31536000, immutable`. Vite
  emits content-hashed filenames (`index-<hash>.js`, `index-<hash>.css`), so
  a 1-year immutable cache is safe — every deploy mints new hashes and the
  fresh `index.html` references them.
- Top-level `*.svg`/`*.png`/`*.ico`/`*.woff2`: 1-day public cache (favicon,
  PWA icons, brand assets that aren't hash-fingerprinted).
- `/` (index.html / SPA fallback): `Cache-Control: no-cache`. The entry
  point is always revalidated so a freshly deployed dashboard is picked up
  without a hard refresh.
- `/api/*`: nginx adds no caching of its own; cache headers are whatever the
  controller emits. The PWA service worker treats `/api/*` as `NetworkOnly`
  (never cached) — see the Workbox config.

## Versioning

`VERSION-UI` is the source of truth for the UI image tag and is independent
of the controller's `VERSION` file. Bump rules:

- **Patch** (`1.0.X`): CSS, copy, asset swaps, no behaviour change.
- **Minor** (`1.X.0`): new routes, new components, new client-side
  features (the React 19 rewrite shipped as `1.1.0`).
- **Major** (`X.0.0`): breaking change to the SPA <-> API contract,
  or a CSP/header change that breaks an embedder.

Both `VERSION` and `VERSION-UI` should be bumped in the same commit when a
controller change forces a coordinated dashboard update; otherwise they
move independently.
