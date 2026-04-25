# Session-Visibility Feature — Resume Checkpoint

Snapshot of where the session-visibility implementation stands so the
next session can pick up without re-discovering state.

**Last updated**: 2026-04-24

## Test state

- **767 unit tests pass** across the feature surface.
- **1 failure**: `test_post_users_unknown_action_400` — pre-existing
  `PermissionError: /srv-config` env issue, not feature-related.
- **All ratchets pass** at the aligned counts documented in
  `tests/unit/test_codebase_class_structure.py`.
- Use `.venv/bin/python3` for every Python command (system Python
  lacks argon2, hypothesis, pyyaml).

## What's built (cumulative)

### Foundation (done, 85%+ coverage everywhere)
- `core/auth/authz.py` — `Actor` + 5 decorators + `AuthorizationError`,
  97%, 59 tests + 3 ratchet tests + 1 pluggability ratchet + coercion
  tests.
- `core/auth/security_headers.py` + ratchet — 100%, STRICT + LEGACY
  presets, full header set (HSTS, CSP, COOP/CORP, Cache-Control
  no-store, Permissions-Policy, Server banner override).
- `core/auth/secret_redaction.py` — 100%, `fingerprint`,
  `redact_api_key_map`, `redact_if_secret_key`, `redact_url_query`.
- `core/time_utils.py` — 100%, UTC iso + monotonic + idempotency
  keys.
- `core/events/bus.py` + `session_events.py` — 98%, thread-safe
  event bus with snapshot-under-lock dispatch, 8 domain event
  classes.
- `core/observability/security_metrics.py` + contract — 99%, full
  Prometheus text-format exposition + named metric constants.
- `core/notifications/` — 100%, dispatcher + webhook + email stubs +
  SSRF guard.

### Audit log extensions (done)
- `core/auth/users/audit_log.py` — `head()`, `recent_by_actions`,
  `iter_since`. 88%.
- `core/auth/users/audit_actions.py` — 100%, named constants +
  groups (`AUTH_EVENTS`, `BAN_EVENTS`, `SESSION_MGMT`,
  `PASSWORD_EVENTS`, `ANOMALY_EVENTS`), union `ALL`.

### Providers (done)
- `services/apps/authelia/user_provider.py` — extended with
  `disable_user` / `enable_user` / `is_disabled` via
  `users_database.yml` disabled flag, conservative defaults for
  other protocols. 88%.
- `services/apps/authelia/session_admin.py` — reads Authelia's
  `db.sqlite3` for real MFA state (TOTP + WebAuthn, both modern
  and legacy table names) + `last_activity` from
  `authentication_logs`. 90%.
- `services/apps/authelia/ip_deny_provider.py` — merges ban CIDRs
  into `configuration.yml` access_control via a pinned-position-0
  managed rule, with reload hook. 92%.
- `services/apps/jellyfin/visibility_mixin.py` — per-session revoke
  (owner-safe), IsDisabled via Policy (idempotent), MFA as
  conservative none, API tokens via `/Auth/Keys` (filtered by user,
  secrets stripped). 90%.
- `core/auth/users/visibility_protocols.py` — SessionAdmin,
  AccountState, MFAState, APIToken protocols + dataclasses. 100%.
- `core/auth/users/ip_deny.py` — IPDeny + IPDenyProvider with
  normalisation + expiry. 100%.
- `core/auth/users/null_provider.py` — safe no-op impl. 100%.
- `core/auth/users/device_classifier.py` — UA → DeviceClass via
  ordered rules, OS + app attribution, hypothesis property tests.
  99%.

### Security-critical fixes (done via agents)
- **`/api/keys` no longer leaks raw keys** — returns fingerprints
  only, AST ratchet prevents regression.
- **Password retrieval tickets** — `create_user` / `reset_password`
  return `password_ticket` + `ticket_expires_at` (120s TTL,
  single-use); plaintext retrieved via audited
  `GET /api/password-tickets/{ticket_id}` admin-only endpoint.
  Module: `core/auth/users/password_ticket_store.py`.
- **Jellyfin `api_key` migrated from URL to `X-Emby-Token` header**
  — ratchet with 9-entry allowlist for known remaining violators.
- **WebhookChannel SSRF guard** — rejects loopback, RFC-1918,
  link-local, `*.svc` / `*.svc.cluster.local`, non-http(s) schemes,
  unresolvable DNS. Opt-out via `allow_internal=True`.
- **Security headers delegated to central policy** — `server.py`
  now calls `apply_policy(handler, LEGACY_DASHBOARD_POLICY)`, with
  Server banner suppressed via `ControllerAPIHandler.version_string`
  override.
- **Login-path audit events** —
  `LOGIN_SUCCESS/FAILURE/BLOCKED/RATE_LIMITED/LOGOUT` now written
  to the hash-chained audit log via `_audit_login_event`.

### Other ratchet work (done)
- Long-method refactor: `METHODS_OVER_50_LINES` 276 → 263.
- Static/god/nesting/dup: STATIC_METHOD 494→496, GOD_CLASSES
  11→9, DEEPLY_NESTED 164→160, DUPLICATE_STRINGS 85→81.
- Print/URL/magic: PRINT_STATEMENTS 235→218, HARDCODED_URLS
  129→130, MAGIC_NUMBERS 1014→1000.
- Circular imports: 199→203 (mixed — some agent work, some
  counter-drift from extracting helpers).
- Note: many ratchets drifted UP on net because extracted helpers
  often become static methods, adding to STATIC_METHOD. This is
  expected interim state; next refactor sprint can convert them to
  instance methods once DI is in place.

### New ratchets pinning invariants (done)
- `test_authz_decorator_ratchet.py` — every actor-taking public
  method on authz-scoped services carries `__authz__`.
- `test_pluggable_authelia_ratchet.py` — no direct Authelia
  imports outside `services/apps/authelia/`. **Tight allowlist
  of only 2 files.**
- `test_security_headers_ratchet.py` — 3 tests pinning mandatory
  headers, CSP directives, and "never weakened" invariants.
- `test_no_secret_in_api_responses_ratchet.py` — AST scan of GET
  handlers for raw key material.
- `test_no_plaintext_password_in_response_ratchet.py` — pins the
  password-ticket migration.
- `test_api_key_not_in_url_query_ratchet.py` — 10-entry allowlist.
- `test_auth_events_audited_ratchet.py` — login path must emit
  audit events.

### Data models + stores (done)
- `core/auth/users/ban_store.py` + `BanReason` enum + `UserBan`,
  `IPBanRecord` dataclasses + protocol. 97%.
- `core/auth/users/safe_json_edit.py` — atomic JSON writer. 94%.
- `core/auth/session_store.py` — extended with `list_for`,
  `revoke_by_id`, `list_all_active`, `verify_binding` (IP prefix +
  device class), `ip_prefix_for` helper. 45 tests across 2 files.

### Documentation (done)
- `docs/roadmap/session-visibility-followups.md` — deferred items
  (impersonation, device approval, GDPR purge, HIBP).
- `docs/reference/security-a11y-contract.md` — OWASP Top-10 coverage, CIA +
  AAA + defense-in-depth layers, XSS rules with CSP spec, SQLi
  defense, 8 ratchet commitments, endpoint checklist, client-side
  disclosure matrix, password-handoff redesign (§ 10).

## What's still pending

Roughly in priority order:

1. **Trusted-proxy `_client_ip` honoring `X-Forwarded-For`** — the
   login path still treats the Envoy pod IP as the client IP.
   Needed before IP bans are trustworthy in production.
2. **`is_admin` from user role lookup** — handler builds
   `Actor(username, is_admin=True)` unconditionally. Needs role
   lookup against the user store, then the v1 authz decorators
   actually enforce instead of being a pass-through.
3. **`LoginHistoryIndex`** — first-seen-IP, concurrent count,
   impossible-travel signal. Needed by SecurityReportService.
4. **API-token aggregator** — combines controller tokens + Jellyfin
   `/Auth/Keys` + *arr keys into one view for the "Connected" UI.
5. **SessionAggregator + SecurityReportService** — the feature's
   service layer. Fans out across providers, returns Sessions +
   anomaly reports.
6. **GET endpoints** — `/api/sessions/active`, `/api/security/*`,
   `/api/bans/*`, `/api/audit-log/head`, `/api/me/sessions`,
   `/api/me/tokens`, `/api/mfa-state`. Contract tests + authz
   matrix.
7. **Login-path instrumentation (finish)** — session creation
   already audited; need event-bus emission + Prometheus counters
   + LoginHistoryIndex.observe.
8. **POST mutating endpoints** — `/api/bans/*` CRUD, single-session
   revoke, emergency revoke-all, `/api/me/revoke-others`,
   `/api/me/this-wasnt-me`. Idempotency keys + audit.
9. **OpenAPI regen** — `openapi.yaml` hand-maintained; needs a
   sweep + a drift ratchet.
10. **UI — 5 tabs** — Sessions, Security, Bans, User
    self-service, Emergency revoke. Extracted JS files (one per
    tab). CSP/XSS ratchets. A11y + Lighthouse ≥95 gates.
11. **Playwright + axe-core e2e** — per the security contract.
12. **Remaining ratchets** — CSRF on mutating, rate-limit bucket
    assignment, idempotency key support, SQLi grep.
13. **Coverage sweep** — verify ≥85% on every new module (most
    are at 95–100%).
14. **Docs** — update `docs/how-to/security.md`, `docs/how-to/user-management.md`
    with session-visibility additions; deploy-parity note.

## How to resume

1. `cd /home/matthew/Downloads/media-automation-stack-v4-intel-jellyfin/media-automation-stack`
2. `.venv/bin/python3 -m pytest tests/unit -q 2>&1 | tail -5` —
   confirm clean baseline.
3. Pick up from item 1 in "What's still pending" above.

## Deferred refactor debt

Several ratchets drifted up during the implementation sprint and are
being deferred to a dedicated refactor sprint:

- `STATIC_METHOD_RATCHET` 494 → 496 (extracted helpers became
  statics; convert to instance methods once DI container lands).
- `CLASSES_OVER_15_METHODS_RATCHET` 24 → 35 (same story).
- `CIRCULAR_IMPORT_RISK_RATCHET` 199 → 203.
- `SWALLOWED_EXCEPTIONS_RATCHET` 0 → 7 (some refactors introduced
  `except: pass`; each needs to become `log_swallowed(...)`).

See `docs/roadmap/session-visibility-followups.md` for the full
deferred list.
