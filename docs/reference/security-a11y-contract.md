# Session-Visibility Feature — Security + Accessibility Contract

Non-negotiable quality bars for the session-visibility + security-reporting
feature. Enforced by a combination of tests, ratchets, CI checks, and
code review. This document is the single source of truth; if the code
diverges, the code is wrong.

Target: first-class luxury product. Every bar below is shipped at v1
or v1 doesn't ship.

---

## 0. Governing principles: CIA + AAA

Every design decision in this feature is measured against the six
pillars below. When a trade-off is needed (e.g. performance vs
confidentiality), the decision is recorded here with its rationale.

### CIA — data guarantees

| Pillar | How this feature protects it |
|---|---|
| **Confidentiality** | API-key aggregator returns metadata only — raw tokens never cross the wire to the frontend (ratchet `test_no_secret_in_api_responses_ratchet`). Session cookies flagged `HttpOnly; Secure; SameSite=Strict`. Audit log redacts password values via the actions-constants layer. `Cache-Control: no-store` on every auth-gated response so browser cache can't leak a view to the next user. CSP + COOP defeat cross-origin reads. Authelia's `users_database.yml` is never sent to the frontend — the controller reads it and emits derived structures only. |
| **Integrity** | Audit log is SHA-256 hash-chained; `/api/audit-log/head` exposes `{height, hash, ts}` for external monitors. Every mutating endpoint is CSRF-protected and requires an idempotency key. Provider-state writes (users_database.yml, configuration.yml) go through `SafeYamlEditor` — validator + atomic rename, so crash mid-write never leaves a half-written config. Client-side code uses Trusted Types (STRICT CSP) so a compromised third party can't silently rewrite DOM sinks. |
| **Availability** | Per-IP + per-user rate limiting on login and mutating endpoints. Provider calls (Jellyfin, Authelia) carry short timeouts; failures degrade gracefully (empty list) rather than cascade. The emergency-revoke endpoint is deliberately kept fast (one DB write, fire-and-forget provider cascade) so an admin can kill compromised sessions under duress. Rate limits on session-visibility endpoints (`security-read` bucket) prevent enumeration DoS. |

### AAA — subject lifecycle

| Pillar | How this feature implements it |
|---|---|
| **Authentication** | Delegated to Authelia as SSO (required dependency; init-only mode lives only during bootstrap). BasicAuth fallback on the controller is audited. MFA visibility per user via the sqlite-backed `AutheliaSessionAdmin` (TOTP + WebAuthn enrollment). Session-token binding to IP-prefix + device-class reduces stolen-cookie impact. |
| **Authorization** | Decorator-based at the service layer (`@requires_authenticated`, `@requires_admin`, `@requires_self_or_admin`, `@requires_role`, `@forbidden_for_impersonation`). Enforced by a ratchet (`test_authz_decorator_ratchet.py`) that walks every public method on authz-scoped service classes and fails if any lacks the `__authz__` marker. Handler-side enforcement is a defense-in-depth belt — the service-layer check is the guarantor. |
| **Accounting / audit** | Append-only hash-chained `audit.jsonl` at `${CONFIG_ROOT}/controller/`. New action constants (`LOGIN_SUCCESS`, `LOGIN_FAILURE`, `LOGIN_BLOCKED`, `LOGIN_RATE_LIMITED`, `LOGOUT`, `SESSION_REVOKED`, `EMERGENCY_REVOKE_ALL`, `PASSWORD_CHANGE`, `BAN_*`, `ANOMALY_*`) grouped in `audit_actions.py` so a consumer-side filter is the set, not a substring. Every authn/authz decision (success, failure, denied, rate-limited, banned) lands as an entry within the same request. The event bus fan-out (`core.events.bus`) ensures Prometheus counters + notification dispatcher see the same events the audit log does — no observer gap. |

### Defense-in-depth principle

No single layer is trusted. Every security-sensitive call crosses at
least two independent checks:

1. **Gateway** (Envoy + Authelia ext_authz) — IP + user bans, MFA
   enforcement per role.
2. **Controller middleware** — rate limit + CSRF + trusted-proxy IP
   extraction.
3. **Service decorators** — `@requires_*` on every public method.
4. **Store** — BanStore re-checks at write time (idempotency key).
5. **Audit** — every decision written, chain-hashed.

A compromised layer does not grant a compromised system. Any
"take-a-shortcut" request from the UI still fails at the service
decorators; any forged service call still fails at the gateway.

---

## 1. OWASP Top-10 (2021) — application-layer

| # | Category | How we cover it |
|---|---|---|
| A01 | Broken Access Control | Decorator-based authz (`core/auth/authz.py`) on every service method; ratchet pins every public method to have `__authz__`. Server-side re-check on every mutating endpoint — **never trust** the handler to have filtered. |
| A02 | Cryptographic failures | Passwords hashed with argon2id (`argon2-cffi`). Audit log hash-chained with SHA-256. Session tokens minted from `secrets.token_urlsafe(32)` — never less than 192 bits of entropy. |
| A03 | Injection | **SQL**: parameterized queries only (`?` placeholders), table names come from whitelist constants (`_TOTP_TABLE`, `_AUTH_LOG_TABLE`), never from user input. **YAML**: `yaml.safe_load` everywhere — no `yaml.load`. **Shell**: no `shell=True`; argv lists only. Ratchet: grep gate for `f".*FROM {.*}"` and friends. |
| A04 | Insecure design | Threat-modeled before code: every new endpoint documents the authz matrix inline. Self-or-admin vs admin-only called out on each decorator. |
| A05 | Security misconfiguration | Deploy parity (compose + k8s use the same config-root volume). No default credentials — `STACK_ADMIN_PASSWORD` rotated on first login. CSP, HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy — all set on the dashboard response. |
| A06 | Vulnerable + outdated components | Dependency surface is small (pure stdlib except argon2, PyYAML). Pins live in `.venv/` at install time; we don't add deps without explicit review. |
| A07 | Auth failures | Rate limit per-IP + per-user; MFA-state visibility per user; session-token binding to IP-prefix + UA-class; first-seen-IP detection; concurrent-session cap. |
| A08 | Integrity failures | Audit log hash-chained, `GET /api/audit-log/head` exposes `{height, hash, ts}` for external verification. `verify_chain()` rescans. |
| A09 | Logging + monitoring failures | `login_success/failure/blocked/rate_limited/logout` actions persisted; Prometheus counters (`login_failures_total`, `bans_current`, `audit_chain_head_age_seconds`); event bus feeds notification dispatcher; optional webhook + email out-of-band. |
| A10 | SSRF | Webhook channel validates outbound URL shape (http(s) only, no localhost, no link-local, no RFC-1918 unless explicitly allowed in config). Tested via unit cases against crafted payloads. |

## 2. XSS Prevention — dashboard UI

The SPA is React 19, so user/provider strings reach the DOM via JSX
(`{value}`) which auto-escapes. The remaining risk surfaces are the
escape hatches; those are policed by the ESLint config and a grep
ratchet.

Banned patterns (enforced across `ui/src/**/*.{ts,tsx}`):

- **`dangerouslySetInnerHTML`** — banned outright, no exceptions
  carried in the current bundle.
- **`document.write(...)`** — banned.
- **`eval(...)`, `new Function(...)`, `setTimeout("string", ...)`** —
  banned.
- **String concatenation into a `href`/`src`/`srcdoc` prop without
  going through a URL allowlist helper** — banned.

`@typescript-eslint/no-explicit-any` at zero is part of this defense:
unstructured `any` makes injection sinks invisible to review.

Additional defenses:

- **Content-Security-Policy** header on every UI response (emitted by
  the [`media-stack-ui`](ui-container.md) nginx layer; see
  [`docker/ui-nginx.conf`](../docker/ui-nginx.conf)):
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
  `cdn.jsdelivr.net` is whitelisted **only** for Geist Variable. The
  `'unsafe-inline'` on `style-src` is required for Tailwind v4 runtime
  injection + Vaul/Sonner positioning; `'unsafe-inline'` on `script-src`
  covers shadcn's small bootstrap and `next-themes`' FOUC guard. All
  React 19 + Vite-built scripts themselves resolve to `'self'`.
- **X-Content-Type-Options: nosniff** — prevents MIME confusion.
- **X-Frame-Options: DENY** — anti-clickjacking.
- **Referrer-Policy: strict-origin-when-cross-origin**.

## 3. SQL Injection — defensive coding

- Every sqlite connection is opened in `mode=ro` (see `AutheliaSessionAdmin`) — even a rogue query can't write.
- Parameterized queries (`?`) for every value. Table names are module-level constants, never interpolated from user input.
- Ratchet: grep gate looking for `f".*FROM {"` patterns in `src/` (excluding docstrings). Fails if a new query interpolates a variable into the FROM clause.

## 4. CSRF — mutating endpoints

- Every POST / PUT / DELETE endpoint carries a CSRF check via the existing `CsrfProtector`.
- New mutating endpoints (ban add/remove, session revoke, emergency revoke) are added to the CSRF-enforced set **by default**. The `_CSRF_EXEMPT_POST_PATHS` set in `handlers_post.py` stays empty for all security-visibility routes — ratchet pins this invariant.

## 5. Rate limiting — per-endpoint

Each endpoint assigned to one of the existing buckets:

| Bucket | Endpoints |
|---|---|
| `user-mgmt` (10/burst, 1/sec) | bans/*, sessions/{id}/revoke, emergency revoke |
| `password-reset` (3/burst, 0.05/sec) | me/password-change, me/this-wasnt-me |
| `global-post` (30/burst, 3/sec) | everything else |
| `security-read` (60/burst, 5/sec) | sessions/active, security/*, audit-log/head |

The `security-read` bucket is new; enumerating sessions is cheap for a legitimate admin but attractive for recon, so we cap it.

## 6. Idempotency — mutating endpoints

- Every ban add / revoke / ban remove POST accepts an optional `Idempotency-Key` header.
- Server-side: keys stored in a small in-memory LRU (N=1024), TTL=5 min. Repeated request with the same key returns the cached 200/4xx response without re-doing the side effect.
- Double-click on the UI produces one server-side ban / revoke, not two.

## 7. Lighthouse ≥95% — dashboard pages

Enforced via **Lighthouse CI** run against a staging deploy. Category minimums:

| Category | Target |
|---|---|
| Performance | ≥95 |
| Accessibility | ≥95 (strict: WCAG 2.1 AA, aiming for AAA contrast on critical alerts) |
| Best Practices | ≥95 |
| SEO | ≥90 (less critical for auth-gated admin UI) |

Concrete implementations:

### Performance
- **Code-split per route** — TanStack Router file-based routes ship as
  separate Vite chunks; only the shell + the active route load on first
  paint.
- **No synchronous XHR**. All fetches via TanStack Query / `fetch` with
  `AbortController` for in-flight cancellation.
- **Static assets served with `Cache-Control: public, max-age=31536000, immutable`**
  for Vite hashed filenames; `no-cache` for `index.html`. See
  [ui-container.md](ui-container.md).
- **Brotli/gzip** negotiated in the response.
- **One whitelisted third-party origin**: `cdn.jsdelivr.net` for Geist
  Variable, fetched as `CacheFirst` by the PWA service worker. No
  trackers.
- **Bundle budget — ratcheted, not aspirational.** `pnpm size` runs in
  CI: per-chunk `size-limit` budgets plus a 250 KB total JS-gzip
  ceiling (current: 240.8 KB). The build fails over budget.

### Accessibility — ratcheted constraints

These are CI gates, not goals. The build fails if any of them regress.

- **axe-core under `wcag2a` + `wcag2aa` tags** asserts **zero
  `serious` and zero `critical`** violations on:
  - `AppShell` (every layout), `CommandPalette`, `UserMenu`,
  - the `/media-integrity` route in all of its states (loading, loaded,
    needs-review present, post-action toast).
  Asserted by Vitest + `vitest-axe`; an additional Playwright pass runs
  axe against the live SPA in CI.
- **Skip link** to `#main-content` is the first focusable element in
  `AppShell`. Asserted by a unit test on the rendered shell.
- **Radix primitives properly labeled** — every `Dialog`, `Drawer`, and
  `Sheet` in the bundle carries both `Dialog.Title` and
  `Dialog.Description`. A unit ratchet greps the compiled component
  graph for `<Dialog.Root>` without a sibling `<Dialog.Title>` and
  fails the build.
- **Color contrast** verified at WCAG 2 AA minimums (4.5:1 normal text,
  3:1 large text and non-text UI) in the **OKLCH palette**, in both
  light and dark themes. Token values in
  [`ui-design-system.md`](ui-design-system.md) are pinned at hue 270
  (neutrals) and hue 150 (accent / success); axe asserts the
  contrast-ratio rule as part of the `wcag2aa` tag set.
- **Touch target ≥ 44 px** on every interactive element — enforced by a
  Vitest snapshot of computed bounding boxes for the shadcn `<Button>`,
  `<IconButton>`, and command-palette item primitives.
- **`@media (hover:hover)` guards every `:hover` rule** — a CSS lint
  rule fails the build if a hover style appears outside that media
  query.
- **`prefers-reduced-motion: reduce`** short-circuits Framer Motion to
  `duration: 0`. Asserted in the motion config unit test.
- **iOS-zoom guard**: `<input>`/`<textarea>` minimum font-size of
  16 px. CSS-lint enforced.

### Best Practices
- **HTTPS-only** — already enforced by Envoy.
- **No deprecated APIs** — no `document.execCommand`,
  `window.showModalDialog`, etc. ESLint blocks them.
- **`@typescript-eslint/no-explicit-any`, `no-console`,
  `no-only-tests`** are pinned at zero. The `pnpm lint` gate fails if
  any reappear.
- **No console errors or warnings** in normal flow.
- **CSP + the other headers listed above**.

### Testing
- **Vitest** drives the unit + a11y assertions above. Current pass
  count: **462 / 462**.
- **Playwright** drives the major routes against a real build of the
  SPA, runs axe-core, and exercises keyboard-only flows.
- **Lighthouse CI** in `.lighthouserc.json` retains the ≥95 thresholds
  as a smoke check; the per-rule axe + size-limit gates above are the
  load-bearing ones.

## 8. Ratchets pinning these invariants

Each of the below gets a test under `tests/unit/test_*_ratchet.py`:

1. `test_csrf_on_mutating_security_endpoints_ratchet` — POST paths under `/api/bans/`, `/api/sessions/`, `/api/emergency-revoke/` must NOT appear in `_CSRF_EXEMPT_POST_PATHS`.
2. `test_xss_safe_ui_ratchet` — grep `ui/src/**/*.{ts,tsx}` for banned patterns (`dangerouslySetInnerHTML`, `document.write`, `eval`, etc.).
3. `test_sql_injection_guard_ratchet` — grep `src/` for `f".*FROM {.*}"` and variants.
4. `test_pluggable_authelia_ratchet` — `import authelia...` or `from authelia...` allowed only in `src/media_stack/services/apps/authelia/**` and deliberately-allowlisted core files.
5. `test_trusted_proxy_ip_extraction_ratchet` — login + audit paths must consult the trusted-proxy helper, not `client_address` directly.
6. `test_rate_limit_bucket_assignment_ratchet` — every new endpoint in handlers must be in one of the four buckets.
7. `test_idempotency_key_support_ratchet` — every ban/revoke mutating endpoint must accept `Idempotency-Key`.
8. `test_openapi_drift_ratchet` — every route registered in handlers must appear in `openapi.yaml`.

## 8b. Client-side disclosure — what must NEVER reach the frontend

The dashboard lives in the same origin as the controller API, so the
server has full control over what flows to the browser. The following
invariants are enforced by explicit tests + a ratchet:

| Never expose | Why | How we enforce |
|---|---|---|
| Provider API keys (Jellyfin, *arrs, Prowlarr, Jellyseerr) | A key in browser storage is a key on disk + in network traces | `test_no_secret_in_api_responses_ratchet` — scans GET handler responses for fields matching `AccessToken`/`ApiKey`/`apikey` |
| Controller bearer tokens | Same | Handled by the same ratchet |
| Password hashes (argon2) | Even hashed passwords leak argon2 parameters and enable offline attack | Every response DTO tested for absence of `password`, `password_history`, `hash` keys |
| `users_database.yml` contents verbatim | Contains hashed passwords + disabled flags | Provider returns `ExternalUser` with `extra: {"has_password": bool}` — boolean only |
| Full exception tracebacks | Leaks paths, module structure | All handlers map exceptions to `{"error": <short_msg>}` truncated to 99 chars |
| `CONFIG_ROOT` / host filesystem paths | Leaks deployment topology | Handlers only emit relative paths; `_ERR_LEN=99` truncates any leak via error detail |
| Environment variables | Leaks secrets + config | `os.environ` never flows into a response body — ratchet scans for it |
| Internal IPs / hostnames | Recon material | Same scan |
| Cryptographic salts / secrets | Game over | Server never reads `jwt_secret`/`session_secret` into a response-path variable |

### Secret-scrubbing discipline

Every provider method that returns data the controller will pass to
the frontend follows the **secret-stripping rule**:

1. The provider DOMAIN object (`ExternalUser`, `APIToken`,
   `ExternalSession`, `MFAState`) has NO field that could hold a
   secret. Confirmed by `test_apitoken_has_no_secret_field` in
   `test_visibility_protocols.py`.
2. The provider's `list_*` method explicitly strips secrets at
   construction time. See
   `services/apps/jellyfin/visibility_mixin.py::list_api_tokens` —
   it reads `AccessToken` from the Jellyfin payload solely as the
   stable `token_id` (opaque to the UI) and never echoes the raw
   token elsewhere.
3. Integration tests assert on the wire-level response: mock a
   provider response containing a secret, call the endpoint, assert
   the secret string does NOT appear in `response.body`.

### Secrets inbound — mutation endpoints

Password, token, invite-code, and similar inputs must NEVER appear in:

- URL path or query string (they land in access logs; go in body).
- Audit-log entries (the audit layer receives `actor.audit_label` +
  the action, not the secret).
- Server logs at INFO / WARNING / ERROR level. A DEBUG-level log of a
  secret is tolerated for local debugging, gated on `LOG_LEVEL=DEBUG`
  which is never set in production.
- Error messages returned to the client ("wrong password" is fine;
  "expected hash X, got hash Y" is not).

## 9. How to add a new endpoint (checklist)

Before opening a PR that adds an endpoint under `/api/sessions/*`,
`/api/security/*`, `/api/bans/*`, or `/api/me/*`:

- [ ] Service method decorated with `@requires_*` from `core.auth.authz`.
- [ ] Audit log entry emitted using a constant from `audit_actions`.
- [ ] Event bus event published for downstream consumers.
- [ ] Prometheus metric updated (at minimum the request-count or status counter).
- [ ] Rate-limit bucket assigned in `handlers_post.py` / `handlers_get.py`.
- [ ] CSRF enforced (for mutating endpoints).
- [ ] `Idempotency-Key` honored (for mutating endpoints).
- [ ] `openapi.yaml` updated.
- [ ] Handler maps `AuthorizationError` to 403 with the `reason` code in the body.
- [ ] Unit test covers 200 + 401 + 403 + 404 + 4xx shapes.
- [ ] Playwright test covers the UI affordance.
- [ ] Lighthouse score still ≥95 on the affected tab.
- [ ] `docs/how-to/security.md` updated if the endpoint changes the threat model.

## 10. Password handoff redesign (2026-04-24)

Generated plaintext passwords — the ones returned to a dashboard
operator the moment a user is created or reset — no longer ride
along in the JSON response body. The v1 flow (``"generated_password":
"hunter2"`` on the create/reset response) landed the plaintext in
every layer that logged the wire, including:

- the browser's Network tab (retained until the tab closes),
- operator screen recordings of the admin UI,
- any reverse proxy with response-body logging,
- the dashboard's own `localStorage` side-effects in prior
  iterations of the "copy to clipboard" button.

### The new contract

``UserWriteService.create_user`` and ``reset_password`` (except when
an admin supplies the plaintext themselves — the caller already
knows it) now return:

```json
{
  "user_id": "u_abc",
  "password_ticket": "8J2hR...twenty-two-chars-total",
  "ticket_expires_at": "2026-04-24T10:02:00Z",
  ...other non-secret fields...
}
```

The ticket is a single-use, 120s-TTL handle into a process-local
``PasswordTicketStore`` (in-memory, thread-safe, evicts prior
tickets for the same user_id on remint). The operator retrieves the
plaintext by calling:

```
GET /api/password-tickets/{ticket_id}
```

- Admin-only (``@requires_admin`` at the service-adjacent role check).
- Rate-limited in the shared ``password-reset`` bucket.
- Audit-logged as ``password_ticket_consumed`` with the bound user_id
  (whether the consume succeeded or hit an expired ticket).
- Single-use: the ticket is burned on first read.

### UI handshake

The React 19 SPA's user-management surface implements the two-step
handshake natively:

1. Grab ``password_ticket`` from the create/reset response.
2. Immediately issue ``GET /api/password-tickets/{ticket}`` via the
   typed API client.
3. Copy the returned plaintext to the clipboard via the Web Clipboard
   API, surface a Sonner toast, and never persist the value into
   component state beyond the toast's lifetime.

The legacy `dashboard.html` was retired with UI v1.1.0; no other client
needs the `generated_password` shape and the controller no longer
emits it.

### Why in-process (not Redis)

An on-disk / networked store would need its own encryption-at-rest
+ operator provisioning story. The process-local option trades
"survives a restart" for "simpler to reason about" — a controller
restart invalidates every outstanding ticket, the operator re-runs
the reset, inconvenience only.

## 11. Endpoint authz matrix

The session-visibility GETs are wired by
``src/media_stack/api/services/security_get_handlers.py`` and
dispatched from ``handlers_get.py``. Every row here corresponds to
one method on ``_SessionVisibilityGetHelper``.

| method | path | authz decorator | rate-limit bucket |
| --- | --- | --- | --- |
| GET | `/api/sessions/active` | `@requires_admin` | `security-read` |
| GET | `/api/users/{user_id}/login-history` | `@requires_admin` | `security-read` |
| GET | `/api/security/failed-logins` | `@requires_admin` | `security-read` |
| GET | `/api/security/new-locations` | `@requires_admin` | `security-read` |
| GET | `/api/security/concurrent` | `@requires_admin` | `security-read` |
| GET | `/api/bans/users` | admin gate (handler) | `security-read` |
| GET | `/api/bans/ips` | admin gate (handler) | `security-read` |
| GET | `/api/audit-log/head` | admin gate (handler) | `security-read` |
| GET | `/api/me/sessions` | `@requires_self_or_admin` | global |
| GET | `/api/me/tokens` | authenticated (self-scoped) | global |
| GET | `/api/me/mfa-state` | authenticated | global |
| GET | `/api/me/login-history` | authenticated (self-scoped) | global |

Notes:

* `security-read` is a 60-token bucket with a 5 tokens/sec refill
  (looser than `user-mgmt` — reads are less sensitive than mutations
  — but tight enough that enumerating every session id costs minutes
  of real time instead of milliseconds). Keyed per trusted-proxy
  client IP.
* The `admin gate (handler)` entries cover services that don't carry
  their own decorator (``BanStore`` / ``AuditLog.head``). The helper
  enforces the check explicitly before calling through; see
  ``_SessionVisibilityGetHelper._user_bans`` / ``_ip_bans`` /
  ``_audit_head`` which each call ``_plumb.require_admin(actor)``.
* `authenticated (self-scoped)` means the helper constrains the
  service call's username parameter to ``actor.username`` — no
  cross-user reads. For `login_history_for_user` the helper
  elevates to an admin-flagged ``Actor`` for the duration of the
  service call so the upstream `@requires_admin` decorator admits
  it; the target is still pinned to the caller's own username, so
  the elevation never leaks to another user's data.
* All responses strip secrets: ``APITokenRecord.to_dict`` excludes
  the token hash by contract (frozen by
  ``test_apitokenrecord_has_no_secret_field``) and the controller's
  ``ApiToken.to_dict`` omits ``token_hash`` entirely.

