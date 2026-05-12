# Security

Media Stack ships with a security baseline that every HTTP service in the stack is measured against. The checks are codified as executable tests under [tests/security/](../../tests/security/), so each service's state is reportable as a pass/fail matrix and enforceable as a CI gate.

Open items and future hardening live in [Security Roadmap](../architecture/security-roadmap.md).

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

Each suite reuses [`SecurityAuditRunner`](../../tests/security/security_audit.py) against an [`AuditTarget`](../../tests/security/security_audit.py) pointing at the service. The runner is **pure HTTP** — it imports no stack code and can audit third-party apps (Jellyfin, Sonarr, Radarr, etc.) the same way.

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

- **Edge TLS** — Envoy terminates TLS on 443 with a self-signed cert auto-minted on first boot (see `_resolve_or_mint_certs` in [compose/edge/providers/envoy/dynamic_config.py](../../src/media_stack/core/platforms/compose/edge/providers/envoy/dynamic_config.py)). Port 80 redirects to 443. Authelia 4.38+ cookies require HTTPS.
- **Admin bootstrap is seed-only** — `STACK_ADMIN_USERNAME` / `STACK_ADMIN_PASSWORD` are a one-time seed used to create the initial admin user. On first login the dashboard forces a rotation and the env value is never consulted again. Rotated credentials live in `${CONFIG_ROOT}/controller/users.json` on a dedicated PVC (K8s: `media-stack-config-controller`) so they survive pod restart.
- **K8s NetworkPolicy** — [deploy/k8s/base/edge/networkpolicy.yaml](../../deploy/k8s/base/edge/networkpolicy.yaml). 12 policies enforce a tier-isolated layout: default-deny-all + allow-DNS + Envoy-as-only-edge + controller-receives-only-from-Envoy + apps-only-from-Envoy-or-controller + per-service inter-app rules. Opt in by applying the `power-user` kustomize profile (or adding `networkpolicy.yaml` to your own kustomization). Requires a CNI with NetworkPolicy support (Calico / Cilium / microk8s-cilium).

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

The [`security-baseline-harness`](../../.github/workflows/ci.yml) job runs two layers on every push:

1. **Harness decision tests** — hard pass/fail. `test_security_audit_harness.py` stubs the HTTP client so the 15+ checks can be exercised against synthetic fixtures. If a check starts passing when it shouldn't (e.g. missing headers), these fail immediately.
2. **Live per-service suites** — run against any reachable target found in the runner's network. Defaults to skipping when no service is reachable, so vanilla GHA runners don't fail. Set `CONTROLLER_URL=…`, `JELLYFIN_URL=…`, etc. on a post-deploy job pointing at a staging cluster to promote this to a hard gate.

The pattern: the harness-unit layer means the **quality of the checks themselves** can't regress silently, even when no live target is available. The per-service layer means the **state of each service** is measured whenever a target is present.

## Session visibility & security reporting

The session-visibility feature ([feature contract](../reference/security-a11y-contract.md),
[resume doc](../roadmap/session-visibility-resume.md)) adds a defense-in-depth
layer on top of the baseline.

### What it delivers

- **Aggregated session view** (`GET /api/sessions/active`) — one list
  covering every live session across the controller, Authelia
  (MFA enrollment + last-activity via `db.sqlite3`), Jellyfin
  (`/Sessions`), and future backends. Per row: username, device
  class (TV/phone/tablet/desktop/CLI), client IP, first-seen-IP
  flag, provider-specific session ID, connected-since.
- **Security analytics**:
  - `GET /api/security/failed-logins` — credential-stuffing
    clusters grouped by /24, sorted by attempt count.
  - `GET /api/security/new-locations` — successful logins from a
    (user, /24) pair never seen in the prior 90 days.
  - `GET /api/security/concurrent` — users holding ≥ N live
    sessions (shared-credential / ATO signal).
- **Bans** — `BanStore` under `${CONFIG_ROOT}/controller/bans.json`
  with atomic writes, schema-versioned. User bans cascade to
  Authelia (`disabled: true` flag in `users_database.yml`), Jellyfin
  (`IsDisabled` Policy bit) and revoke all live sessions. IP bans
  sync into Authelia's `configuration.yml` access_control rules
  via a pinned managed rule so Envoy's ext_authz hook enforces
  them at the edge.
- **Emergency revoke** — one admin click revokes every session on
  every provider and rotates short-lived secrets; heavily audited.
- **Audit chain head** — `GET /api/audit-log/head` exposes
  `{height, hash, ts}` for external tamper-evidence monitors.
- **User self-service** — `/api/me/sessions`, `/api/me/tokens`,
  `/api/me/this-wasnt-me`, `/api/me/revoke-others`.

### Security posture (CIA + AAA)

Pillars documented in [security-a11y-contract.md § 0](../reference/security-a11y-contract.md).
Highlights:

- **Confidentiality** — `/api/keys` returns fingerprints
  (`abcd…wxyz`), never raw provider keys. Passwords flow via
  single-use retrieval tickets (`/api/password-tickets/{id}`),
  never in response bodies. `Cache-Control: no-store` on every
  auth-gated response.
- **Integrity** — hash-chained audit log; every mutating endpoint
  is CSRF-protected and accepts `Idempotency-Key`.
- **Availability** — per-IP + per-user rate limiting on login and
  mutating endpoints. `security-read` bucket caps enumeration
  attempts on `/api/sessions` and `/api/security/*`.
- **Authentication** — Authelia SSO is required (file-backend mode
  during init only). MFA state read from Authelia's sqlite for the
  dashboard; per-user MFA enforcement delegated to Authelia.
- **Authorization** — decorator-based at the service layer
  (`@requires_authenticated`, `@requires_admin`,
  `@requires_self_or_admin`, `@requires_role`,
  `@forbidden_for_impersonation`). Enforced by a ratchet.
- **Accounting** — `login_success / login_failure / login_blocked
  / login_rate_limited / logout / session_revoked /
  emergency_revoke_all / password_change / ban_* / anomaly_*`
  actions land in the hash-chained audit log.

### New ratchets (CI-enforced)

12 session-visibility ratchets, all in `tests/unit/test_*_ratchet.py`:

| Ratchet | Scope |
|---|---|
| `test_authz_decorator_ratchet.py` | every authz-scoped service method carries `__authz__` |
| `test_pluggable_authelia_ratchet.py` | no direct Authelia imports outside `services/apps/authelia/` |
| `test_security_headers_ratchet.py` | every canonical preset emits CSP + HSTS + COOP/CORP/Cache-Control + X-Frame + X-CTO + Permissions-Policy |
| `test_no_secret_in_api_responses_ratchet.py` | GET handlers don't echo raw API keys |
| `test_no_plaintext_password_in_response_ratchet.py` | user-service returns `password_ticket`, never `generated_password` |
| `test_api_key_not_in_url_query_ratchet.py` | credentials flow via headers, not URL query |
| `test_auth_events_audited_ratchet.py` | login path writes audit entries |
| `test_trusted_proxy_ip_ratchet.py` | audit IP comes from trusted-proxy helper, not `client_address` |
| `test_csrf_on_mutating_security_endpoints_ratchet.py` | `/api/bans/**`, `/api/sessions/**`, `/api/password-tickets/**` are CSRF-enforced |
| `test_rate_limit_bucket_coverage_ratchet.py` | every security endpoint goes through a rate limiter |
| `test_idempotency_key_ratchet.py` | mutating ban/revoke handlers accept `Idempotency-Key` |
| `test_sqli_static_scan_ratchet.py` | `.execute(...)` calls never interpolate user input into SQL |

### Threat model (quick)

| Threat | Mitigation |
|---|---|
| Stolen session cookie replayed from another IP | `SessionStore.verify_binding` flags IP-prefix / device-class change |
| Credential stuffing | per-IP login rate limit + `login_failure` clusters surfaced in the UI |
| UI double-submit bans IP twice | `Idempotency-Key` header accepted on all ban/revoke endpoints |
| Operator leaks dashboard URL | `Cache-Control: no-store` + auth-gated — browser/proxy never retains |
| Cross-origin steal via `<iframe>` | `X-Frame-Options: DENY` + `frame-ancestors 'none'` + COOP `same-origin` |
| SSRF via notification webhook | `_WebhookUrlValidator` blocks RFC-1918, link-local, loopback, `*.svc` |
| Compromised third-party script | CSP `require-trusted-types-for 'script'` (STRICT preset) + no `unsafe-inline` for new admin pages |

---

## Incident response — leaked secret in git history

The 2026-05-12 incident landed 8 secrets in git history via a
captured `GET /api/backup` JSON committed as a fixture (Google
OAuth pair, Authelia storage encryption key, Bazarr +
*arr secrets). All 8 values were rotated on the live stack
before scrubbing; the Google OAuth pair was revoked at the
provider. The recovery sequence below is the runbook for the
next time something gets committed it shouldn't have been.

### Step 1 — Revoke at the source, immediately

Before touching git history, rotate every leaked secret at its
provider:

* Google / GitHub / Cloud creds — provider console, "delete
  credential" or "revoke OAuth client"
* Stack-internal secrets (`STACK_ADMIN_PASSWORD`, Authelia
  storage key, *arr API keys) — rotate via the dashboard's
  "Reset password" flow or by deleting + re-creating the
  service container so it regenerates

History scrubbing without revocation is theatre — the secret is
already in any clone, archive, scanner cache, and search index
that hit the repo before you noticed. **Rotation is the only
control that actually invalidates the leak.**

### Step 2 — Verify the leak's scope

Find every commit + file that ever touched the secret:

```bash
git log --all -p | grep -nE "<secret-string-or-pattern>"
git log --all --diff-filter=ACMR --pretty=format: --name-only \
    -S "<exact-secret-value>" | sort -u
```

For multi-secret incidents (the 2026-05-12 case had 8), build a
single regex alternation up front so you can re-run the count
after each step:

```bash
git log --all -p 2>&1 | grep -cE "(GOCSPX-…|AIza…|<other-shapes>)"
```

### Step 3 — Backup-mirror the repo before any rewrite

```bash
git clone --mirror . ../my-repo.pre-scrub.git
```

This is the rollback path if filter-repo produces an unexpected
result. Keep it until the force-push has bedded in for ~24
hours.

### Step 4 — Install `git-filter-repo`

```bash
pip install --user --break-system-packages git-filter-repo
# (the --break-system-packages flag is needed on PEP-668-managed
# Python installations; omit it on a venv-based install)
```

### Step 5 — Build a `replacements.txt`

One line per secret, `==>` separator, replacement text. Use a
documented-placeholder pattern so the replacement is itself
greppable:

```
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==>REDACTED-google-oauth-client-secret
744487b958c96db4a3b30a4ead60781d5e8197204384c037f468dd72bf421e9d==>REDACTED-authelia-storage-encryption-key
…
```

### Step 6 — Run filter-repo

```bash
git filter-repo --replace-text replacements.txt --force
```

filter-repo will remove the `origin` remote (safety default) and
record the pre-rewrite history under `refs/original/`. Both have
to be cleaned up for the scrub to be effective.

### Step 7 — Purge the safety refs + repack

```bash
git update-ref -d refs/original/refs/heads/main
git update-ref -d refs/remotes/origin/main 2>/dev/null
git update-ref -d refs/remotes/origin/HEAD 2>/dev/null
git reflog expire --expire=now --all
git gc --prune=now --aggressive
```

### Step 8 — Verify zero residue

```bash
git log --all -p 2>&1 | grep -cE "(<secret-pattern-1>|<secret-pattern-2>)"
# Expected: 0
```

If non-zero, identify the surviving ref (`git for-each-ref` —
look for any `refs/original/*`, `refs/remotes/*`, or stash
entries you missed) and repeat Step 7 against it.

### Step 9 — Force-push

```bash
git remote add origin <url>   # filter-repo stripped this
git push origin main --force
git push origin --tags --force   # only if tag SHAs changed
```

`--force-with-lease` is the safer default for everyday
force-pushes, but it requires a fresh `git fetch` that would
re-pollute the local object store with the un-scrubbed history.
For a post-scrub push, plain `--force` is the right tool —
you've already verified locally that origin has nothing newer
than what you're about to overwrite.

### Step 10 — Notify

* Anyone with a clone of the un-scrubbed history needs to
  `git fetch + git reset --hard origin/main` (or re-clone).
  For a small-team / pre-public repo this is just the operator
  themselves.
* For a public repo, file a security disclosure note in the
  CHANGELOG noting which commits were rewritten and why.
  GitHub's own caches + the fork ecosystem may still retain old
  SHAs for hours — there's no fix for that other than rotation
  (Step 1).

### Step 11 — Add a control so it doesn't recur

The pre-commit hook + CI ratchets shipped 2026-05-12
(`.pre-commit-config.yaml` + `tests/unit/ratchets/
test_no_committed_secrets_ratchet.py`) catch known secret
prefixes at commit-time and CI-time. Adding a new secret type
the ratchets didn't anticipate = one regex line + a baseline
re-scan:

```bash
# Add the new pattern to the regex table in the ratchet test,
# then re-baseline detect-secrets:
detect-secrets scan --baseline .secrets.baseline
git add tests/unit/ratchets/test_no_committed_secrets_ratchet.py \
        .secrets.baseline
```

See `CONTRIBUTING.md`'s "What NOT to commit" section for the
operator-side install + baseline-management workflow.

---

**Project Steward**
Matthew Loschiavo • [matthewloschiavo.com](https://matthewloschiavo.com) • [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com) • [LinkedIn](https://www.linkedin.com/in/matthewloschiavo)
