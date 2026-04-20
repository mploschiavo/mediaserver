# Changelog

All notable changes to this stack. Dates reflect when the work landed on `main`.

## [v1.0.94] — 2026-04-19

### Security
- **Admin bootstrap redesign.** `STACK_ADMIN_PASSWORD` is now a one-time seed
  used only until the first successful login. The dashboard forces a password
  rotation on first login; rotated credentials live in
  `${CONFIG_ROOT}/controller/users.json` and the env value is never consulted
  again. Added `source` field (`env-seed` / `env-legacy` / `rotated`) so
  support can see which path produced a credential.
- **Break-glass recovery.** Deleting `users.json` re-enables the seed
  credential for a single login, documented in `docs/auth-guide.md`.

### Auth
- **Authelia 4.38 OIDC rebuild.** Ground-up rewrite of OIDC config generation.
  New `OidcCrypto` helper emits RSA PEM keys via `openssl` and hashes client
  secrets with `passlib` pbkdf2-sha512 in Authelia's adjusted-base64 format
  (`+` → `.`) — the only form Authelia's internal parser accepts.
- **Declarative OIDC client registry** at `contracts/auth/oidc_clients.yaml`
  with `{base}`, `{sub}`, `{gateway}` placeholders. Moves Jellyseerr client
  registration out of hardcoded Python into a contract that travels with
  configuration.
- **Domain topology auto-detection.** `_resolve_domain_pair` now handles
  flat profiles (K8s, `routing.base_domain` set, no sub) separately from
  nested profiles (compose). Fixes the 2026-04-18 K8s login loop where
  `auth.m.iomio.io` was being emitted instead of `auth.iomio.io`.
- **Secret preservation across regens.** `_reuse_existing_secrets` + placeholder
  detection so `configure_auth` never trashes Authelia's SQLite encryption
  key, which would brick startup on the next boot.

### Infrastructure
- **Compose ↔ K8s parity enforcement.** 13 parity tests across 6 test classes
  in `tests/unit/test_compose_k8s_parity.py` (shared config mounts, env vars,
  admin seed values, image tags, kustomization coverage, state persistence,
  placeholder seeds).
- **Controller state PVC on K8s.** New `media-stack-config-controller` PVC
  (1Gi) mounted at `/srv-config/controller` so `users.json`, audit log,
  API tokens, and password policy survive pod restart — previously ephemeral.
- **Authelia config PVC on K8s.** `/config` is now a PVC instead of `emptyDir`;
  the init container only seeds when empty. Matches compose bind-mount
  semantics so controller-written `configuration.yml` is what Authelia reads.
- **`auth-authelia.yaml` added to kustomization.** `kubectl apply -k k8s/`
  now provisions Authelia; previously required a separate apply.

### Routing
- **Prowlarr UrlBase via API reconciliation.** File-patching `config.xml` is
  insufficient — Prowlarr rehydrates the file from its SQLite DB on startup.
  New `_reconcile_url_base` in `services/apps/servarr/http_preflight.py`
  PUTs `/api/v1/config/host` so the value lands in the DB and survives
  restart. Covers all ARR apps with the correct API version (`v3` for
  Sonarr/Radarr, `v1` for the rest).
- **Envoy prefix vs UrlBase audit test** (`test_envoy_prefix_matches_app_url_base.py`)
  enforces on-disk consistency — if Envoy advertises `/app/<slug>`, the app's
  config must serve from that prefix, or browser assets will 404.

### Distribution
- `bin/regen-dist.sh` regenerates `dist/docker-compose.yml` and
  `dist/k8s-deploy.yaml` from sources; both bundles now pin
  `media-stack-controller:v1.0.94` (previously drifted to `v1.0.1` and
  `v1.0.6` respectively).

## [v1.0.67 .. v1.0.69] — 2026-04-17 .. 2026-04-18

### TLS
- **Envoy auto-mints a self-signed cert** the first time the compose generator
  finds an empty cert dir (`_resolve_or_mint_certs`). HTTPS on 443, HTTP on
  80 redirects to HTTPS. Required for Authelia 4.38 session cookies.
- **Cert upload UI** — dashboard can replace the self-signed cert with a
  user-provided one; controller reloads Envoy after install.
- **Controller-triggered Envoy reload regenerates config first** before
  SIGHUP-ing Envoy, so cert swaps and vhost additions actually land.
- **Copy Hosts button** on the dashboard now emits every Envoy vhost plus
  a sync-hosts script, resolving the "I added an app and `/etc/hosts` is
  out of date" footgun.

## [v1.0.48 .. v1.0.65] — 2026-04-13 .. 2026-04-17

### Security hardening (controller)
- Origin/Referer cross-check on CSRF (v1.0.51)
- IP-based failed-login lockout — 20 fails / 5 min → 15 min 429 (v1.0.52)
- Audit every mutating POST, hash-chained (v1.0.53)
- RBAC `controller_admin` role + session cookies + OIDC redirect hook (v1.0.56)
- Refresh-token pattern + K8s NetworkPolicy (v1.0.57)
- Sudo re-auth gate + webhook HMAC verification (v1.0.59)
- Audit-log chain verifier, auto Envoy reload on cert install (v1.0.64)
- Security event Prometheus counters + session idle timeout (v1.0.65)

### Security baseline harness
- Pure-HTTP audit runner (`tests/security/security_audit.py`) with 19 checks
  across authentication, CSRF/session, response hygiene, and abuse prevention.
- Per-service suites for Controller, Jellyfin, Jellyseerr, Sonarr, Radarr,
  Prowlarr, Bazarr.
- CI gate (`security-baseline-harness` job) runs harness-unit tests on every
  push; live per-service suites run when a target is reachable.

## [v1.0.1 .. v1.0.46] — 2026-04-08 .. 2026-04-13

### Platform foundation
- Controller security hardening: auth by default, bearer tokens, global
  CSRF + rate limit, SSRF block, security headers (v1.0.46).
- User + role management: CRUD API, dashboard UI, Authelia + Jellyfin
  providers, hash-chained audit log.
- Controller v1.0.2: `argon2-cffi` + user-mgmt validator tolerance.
- Class-based architecture refactor (v1.0.6).
- Home screen rails, qBit categories, Maintainerr path (v1.0.5).
- TRASHguides custom-format import API (Phase 3b).
- Configure-auto-scan job for Sonarr/Radarr → Jellyfin (Phase 3a).
- Bootstrap DAG: configure-auth, configure-indexers, configure-arr-clients
  jobs wired through the jobs framework.

## [v1.0.0] — 2026-04-07

- Initial release: images pushed to `harbor.iomio.io`, all manifests pinned.
