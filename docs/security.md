# Security

Media Stack ships with a security baseline that every HTTP service in the stack is measured against. The checks are codified as executable tests under [tests/security/](../tests/security/), so each service's state is reportable as a pass/fail matrix and enforceable as a CI gate.

Open items and future hardening live in [Security Roadmap](internals/security-roadmap.md).

## Run the baseline

```bash
# Audit the controller
CONTROLLER_URL=http://localhost:9100 \
CONTROLLER_USER=admin \
CONTROLLER_PASS=<pw> \
    python -m pytest tests/security/test_controller_security_baseline.py -v

# Audit another service (Jellyfin, Jellyseerr, ...)
python -m pytest tests/security/test_jellyfin_security_baseline.py -v

# Aggregate matrix across all services
python -m pytest tests/security/ -v
```

Each suite reuses [`SecurityAuditRunner`](../tests/security/security_audit.py) against an [`AuditTarget`](../tests/security/security_audit.py) pointing at the service. The runner is **pure HTTP** — it imports no stack code and can audit third-party apps (Jellyfin, Sonarr, Radarr, etc.) the same way.

## The 19 baseline checks

Every service is measured against these. A service declares which paths are public / sensitive / mutating, and the runner probes each.

### Authentication & access control

| # | Check | Meaning | Critical |
|---|---|---|---|
| 1 | `public_endpoints_allow_unauth` | Paths the service advertises as public (`/healthz`, `/readyz`) return 200 without auth. | yes |
| 2 | `sensitive_paths_require_auth` | All `/api/*`, `/metrics`, `/logs/*` return **401** without auth. | **yes** |
| 3 | `authenticated_access_succeeds` | Same sensitive paths return 200 with valid basic auth. Catches over-tight locks. | yes |
| 4 | `wrong_creds_rejected` | Bad username/password returns 401, not 200. | yes |
| 5 | `bearer_admin_works` | A minted admin-scope bearer token authenticates a GET. (Skipped if the runner doesn't mint one.) | yes |
| 6 | `bearer_read_blocks_mutation` | A `read`-scope bearer token is rejected on POST/PUT/DELETE with 401/403. | yes |
| 7 | `revoked_bearer_rejected` | A revoked bearer token returns 401. | yes |

### Session / CSRF

| # | Check | Meaning | Critical |
|---|---|---|---|
| 8 | `csrf_blocks_cookie_no_token` | Cookie-bearing POST without a matching `X-CSRF-Token` returns 401/403. | **yes** |
| 8b | `cross_origin_mutation_rejected` | A cookie-bearing POST with a cross-origin `Origin` header is rejected even with a valid CSRF token. Defense-in-depth against token theft. | **yes** |

### Response hygiene

| # | Check | Meaning | Critical |
|---|---|---|---|
| 9 | `security_headers` | Every response carries `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Content-Security-Policy`, `Strict-Transport-Security`. | yes |
| 10 | `hsts_value` | HSTS includes `max-age` ≥ `31536000` and `includeSubDomains`. | yes |
| 11 | `csp_default_src` | CSP defines `default-src` and `frame-ancestors`. | yes |
| 12 | `no_secret_in_errors` | 401/400 bodies don't contain obvious password / API-key / bearer-token patterns. | **yes** |
| 12b | `credential_endpoints_no_echo` | `/api/keys` (and similar admin-creds endpoints) never echo the plaintext password even when called authenticated. | **yes** |
| 12c | `trusted_proxy_spoof_rejected` | Setting `Remote-User` (or the configured proxy identity header) from an IP **not** in the trusted-proxy CIDR list does not authenticate the request. | **yes** |

### Abuse prevention

| # | Check | Meaning | Critical |
|---|---|---|---|
| 13 | `rate_limit_triggers` | Hammering a mutating endpoint eventually yields 429. | yes |
| 14 | `body_size_cap` | Oversized request body (2 MiB) is rejected with 400/413 instead of buffered. | **yes** *(open — see roadmap)* |
| 15 | `webhook_ssrf_block` | Registering a private-IP webhook URL is rejected. Blocks SSRF via webhook registration. | **yes** |
| 16 | `trailing_slash_canonicalization` | Adding/removing a trailing slash doesn't bypass auth. | yes |

## Infrastructure hardening

- **Edge TLS** — Envoy terminates TLS on 443 with a self-signed cert auto-minted on first boot (see `_resolve_or_mint_certs` in [compose/edge/providers/envoy/dynamic_config.py](../src/media_stack/core/platforms/compose/edge/providers/envoy/dynamic_config.py)). Port 80 redirects to 443. Authelia 4.38+ cookies require HTTPS.
- **Admin bootstrap is seed-only** — `STACK_ADMIN_USERNAME` / `STACK_ADMIN_PASSWORD` are a one-time seed used to create the initial admin user. On first login the dashboard forces a rotation and the env value is never consulted again. Rotated credentials live in `${CONFIG_ROOT}/controller/users.json` on a dedicated PVC (K8s: `media-stack-config-controller`) so they survive pod restart.
- **K8s NetworkPolicy** — [k8s/networkpolicy.yaml](../k8s/networkpolicy.yaml). 12 policies enforce a tier-isolated layout: default-deny-all + allow-DNS + Envoy-as-only-edge + controller-receives-only-from-Envoy + apps-only-from-Envoy-or-controller + per-service inter-app rules. Opt in by applying the `power-user` kustomize profile (or adding `networkpolicy.yaml` to your own kustomization). Requires a CNI with NetworkPolicy support (Calico / Cilium / microk8s-cilium).

## Current pass/fail matrix

| Service | passing | failing | skipped | Notes |
|---|---:|---:|---:|---|
| Controller (:9100) | 16 | 0 | 2 | Live baseline green. Bearer-token mint checks skipped until live-token suite lands. |
| Jellyfin (:8096) | 4 | 0 | 3 | Hardening headers expected from Envoy upstream. |
| Jellyseerr (:5055) | 3 | 0 | 1 | Same: Envoy emits hardening headers. |
| Sonarr (:8989) | 3 | 0 | 1 | Servarr family; shared harness via `servarr_baseline.py`. |
| Radarr (:7878) | 3 | 0 | 1 | Same. |
| Prowlarr (:9696) | 3 | 0 | 1 | Uses `/api/v1/` (older API version). |
| Bazarr (:6767) | 2 | 0 | 1 | Behind `/app/bazarr` path prefix in our deploy. |

Each row renders from the test output once the per-service suites are filled in.

## Adding a new service

1. Create `tests/security/test_<service>_security_baseline.py`.
2. Define an `AuditTarget` with:
   - `base_url` (e.g. `http://localhost:8096`)
   - `admin_user` / `admin_pass` from a service-specific env var
   - `public_paths` — advertised probe endpoints
   - `sensitive_paths` — admin / data endpoints
   - `mutating_paths` — one safe no-op mutation per service for rate / CSRF probes
3. Run `pytest -v` and iterate until baseline passes.
4. Track exceptions (e.g. a third-party app that doesn't speak CSRF) in the test file as explicit `expected=skip` notes, so the gap is visible in the matrix.

## CI gate

The [`security-baseline-harness`](../.github/workflows/ci.yml) job runs two layers on every push:

1. **Harness decision tests** — hard pass/fail. `test_security_audit_harness.py` stubs the HTTP client so the 15+ checks can be exercised against synthetic fixtures. If a check starts passing when it shouldn't (e.g. missing headers), these fail immediately.
2. **Live per-service suites** — run against any reachable target found in the runner's network. Defaults to skipping when no service is reachable, so vanilla GHA runners don't fail. Set `CONTROLLER_URL=…`, `JELLYFIN_URL=…`, etc. on a post-deploy job pointing at a staging cluster to promote this to a hard gate.

The pattern: the harness-unit layer means the **quality of the checks themselves** can't regress silently, even when no live target is available. The per-service layer means the **state of each service** is measured whenever a target is present.

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
