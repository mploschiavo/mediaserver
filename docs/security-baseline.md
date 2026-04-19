# Security baseline

<!-- Baseline audit for every HTTP service in the stack. Codified as
     executable tests under tests/security/. -->



The security baseline every service in the stack is measured against.
It's codified as executable tests under [tests/security/](../tests/security/)
so the state of each service can be reported as a pass/fail matrix and
enforced as a CI gate.

## How to use

```bash
# Audit the controller
CONTROLLER_URL=http://localhost:9100 \
CONTROLLER_USER=admin \
CONTROLLER_PASS=<pw> \
    python -m pytest tests/security/test_controller_security_baseline.py -v

# Audit another service (Jellyfin, Jellyseerr, ...) via its own suite
python -m pytest tests/security/test_jellyfin_security_baseline.py -v

# Aggregate matrix across all services
python -m pytest tests/security/ -v
```

Each suite reuses [`SecurityAuditRunner`](../tests/security/security_audit.py)
against an [`AuditTarget`](../tests/security/security_audit.py) pointing
at the service. The runner is **pure HTTP** — it imports no stack code
and can audit third-party apps (Jellyfin, Sonarr, Radarr, etc.) the
same way.

## Checks (19 at baseline)

Every service is measured against these. A service declares which
paths are public / sensitive / mutating, and the runner probes each.

### Authentication & access control

| # | Check | Meaning | Critical? |
|---|---|---|---|
| 1 | `public_endpoints_allow_unauth` | Paths the service advertises as public (`/healthz`, `/readyz`) return 200 without auth. | yes |
| 2 | `sensitive_paths_require_auth` | All `/api/*`, `/metrics`, `/logs/*` return **401** without auth. (The bug that started all this: `/api/users` was returning 200 + full user list.) | **yes** |
| 3 | `authenticated_access_succeeds` | Same sensitive paths return 200 with valid basic auth. Catches over-tight locks. | yes |
| 4 | `wrong_creds_rejected` | Bad username/password returns 401, not 200. Catches accidental bypass. | yes |
| 5 | `bearer_admin_works` | A minted admin-scope bearer token authenticates a GET. (Skipped if runner doesn't mint one.) | yes |
| 6 | `bearer_read_blocks_mutation` | A `read`-scope bearer token is rejected on POST/PUT/DELETE with 401/403. Catches scope escape. | yes |
| 7 | `revoked_bearer_rejected` | A revoked bearer token returns 401. Catches revocation not taking effect. | yes |

### Session / CSRF

| # | Check | Meaning | Critical? |
|---|---|---|---|
| 8 | `csrf_blocks_cookie_no_token` | Cookie-bearing POST without a matching `X-CSRF-Token` returns 401/403. Catches missing CSRF on mutating endpoints. | **yes** |
| 8b | `cross_origin_mutation_rejected` | A cookie-bearing POST with a cross-origin `Origin` header is rejected even with a valid CSRF token. Defense-in-depth against token theft. | **yes** |

### Response hygiene

| # | Check | Meaning | Critical? |
|---|---|---|---|
| 9 | `security_headers` | Every response carries `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Content-Security-Policy`, `Strict-Transport-Security`. | yes |
| 10 | `hsts_value` | HSTS includes a `max-age` ≥ `31536000` and `includeSubDomains`. | yes |
| 11 | `csp_default_src` | CSP defines `default-src` and `frame-ancestors`. Prevents framing + cross-origin asset injection. | yes |
| 12 | `no_secret_in_errors` | 401/400 bodies don't contain obvious password/API-key/bearer-token patterns. Catches accidental echo. | **yes** |
| 12b | `credential_endpoints_no_echo` | `/api/keys` (and similar admin-creds endpoints) never echo the plaintext password even when called authenticated. Regression guard for a real past bug. | **yes** |
| 12c | `trusted_proxy_spoof_rejected` | Setting `Remote-User` (or the configured proxy identity header) from an IP NOT in the trusted-proxy CIDR list does **not** authenticate the request. Catches misconfigured forward-auth. | **yes** |

### Abuse prevention

| # | Check | Meaning | Critical? |
|---|---|---|---|
| 13 | `rate_limit_triggers` | Hammering a mutating endpoint eventually yields 429. Catches missing rate-limit layer. | yes |
| 14 | `body_size_cap` | Oversized request body (2 MiB) is rejected with 400/413 instead of buffered. Blocks DoS via memory exhaustion. | **yes** (currently failing for controller; open TODO) |
| 15 | `webhook_ssrf_block` | Registering a private-IP webhook URL is rejected. Blocks SSRF via webhook registration. | **yes** |
| 16 | `trailing_slash_canonicalization` | Adding/removing a trailing slash doesn't bypass auth. Catches middleware-only checks that miss the canonical path. | yes |

### Beyond the baseline (not yet coded, tracked as TODOs)

- `ip_lockout_on_brute_force` — bursts of failed logins from a single IP trigger cooldown.
- `origin_header_check` — POST with a cross-origin `Origin` header is rejected.
- `content_type_enforcement` — `/api/*` POSTs with `Content-Type` ≠ `application/json` are rejected.
- `audit_log_covers_mutation` — every successful mutation writes an audit entry (requires read access to the audit log, done via `/api/audit-log`).
- `tls_enforced` — if `X-Forwarded-Proto=http` via trusted proxy, the service redirects or refuses.
- `cors_headers_absent` — wildcard `Access-Control-Allow-Origin: *` not set on any response.
- `options_preflight_sane` — `OPTIONS /api/users` returns a strict CORS preflight (or 405 if unused).

## Expected pass/fail matrix (at this commit)

| Service | passing | failing | skipped | Notes |
|---|---:|---:|---:|---|
| Controller (via localhost:9100) | 13 | 1 | 2 | `body_size_cap` still open; bearer checks skipped unless test mints them. |
| Jellyfin (8096) | TBD | | | per-service suite TODO |
| Jellyseerr (5055) | TBD | | | per-service suite TODO |
| Sonarr (8989) | TBD | | | per-service suite TODO |
| Radarr (7878) | TBD | | | per-service suite TODO |
| Prowlarr (9696) | TBD | | | per-service suite TODO |
| Bazarr (6767) | TBD | | | per-service suite TODO |

Each row will render from the test output once the per-service suites
are filled in.

## Adding a new service

1. Create `tests/security/test_<service>_security_baseline.py`.
2. Define an `AuditTarget` with:
   - `base_url` (e.g. `http://localhost:8096`)
   - `admin_user` / `admin_pass` from a service-specific env var
   - `public_paths` = advertised probe endpoints
   - `sensitive_paths` = admin/data endpoints
   - `mutating_paths` = one safe no-op mutation per service for rate/CSRF probes
3. Run `pytest -v` and iterate until baseline passes.
4. Track exceptions (e.g. a third-party app that doesn't speak CSRF) in
   the test file as explicit `expected=skip` notes, so the gap is visible
   in the matrix.

## CI gate

A `security-baseline` GitHub Actions job runs the full audit against a
live cluster on every push. Any regression (a green check flipping to
red) fails the pipeline. Skips are allowed to drift but never to
decrease (a "missing check" in service X is a regression).
