# User Management

End-to-end guide for the user-management feature in the controller
UI (Settings → Users). Covers both the admin runbook (how to add,
invite, reset, remove users) and the end-user runbook (how to accept
an invite, manage your own password and sessions).

The controller is provider-agnostic: Authelia is the source of truth
for authentication, Jellyfin and Jellyseerr are downstream consumers
that get kept in sync automatically. Role → group/policy mappings
live in `contracts/roles.yaml`.

## 1. Admin runbook

### Create a user (password known)

1. Open the controller UI, go to **Settings → Users → Add user**.
2. Enter email, username, display name, and choose a role.
3. Enter a password (the strength meter enforces the policy).
4. Save. The password is propagated to Authelia and to every
   secondary provider that supports password set. If a provider
   auto-provisions on first OIDC login (e.g. Jellyseerr in OIDC
   mode), it is skipped — the user gets linked on first login.

### Invite a user (password stays private)

1. **Settings → Users → Invite**, enter email + role, pick a
   TTL (default 7 days).
2. Copy the invite link. It looks like
   `https://media.<your-domain>/?invite=<token>`.
3. Send the link to the user over a secure channel.
4. The user opens the link, picks their own username, display
   name, and password. The controller creates the user across
   all providers at that point. The admin never sees the password.

### Bulk import

1. **Settings → Users → Bulk import** (CSV paste).
2. Columns: `email,username,display_name,role_slug`. `role_slug`
   is optional, defaults to `adult`.
3. The controller creates each user with a generated password.
   Passwords are returned in the response summary — copy them
   and distribute safely (or use invites instead for new users).

### Reset a password

- Settings → Users → select user → Reset password.
- Supply a password (policy-enforced) or leave blank to generate.
- The new password is pushed to every provider with
  `supports_password=true`. History is stored as salted HMAC so
  the user can't reuse the last 5 passwords.
- Rate-limited to ~1 reset per 20s per target account so
  IP-rotating attackers can't brute-force.

### Revoke sessions (without deleting the user)

- Settings → Users → select user → Revoke sessions.
- Useful after a role change or suspected compromise. Jellyfin
  sessions are force-terminated; Authelia file backend has no
  session store (no-op).

### Delete a user

- Settings → Users → select user → Delete.
- The controller soft-deletes the local row, revokes sessions on
  every provider (best-effort), and calls `delete_user` on each.

### Role editor

- Settings → Users → Roles → edit a role.
- Writable fields: display name, description, `sso_groups`,
  `propagate_to_service_admins`, `require_2fa`, and
  `provider_payloads`.
- Changes are written back to `contracts/roles.yaml` atomically
  via SafeYamlEditor (rolls back if validation fails on reload).
- Existing users get the new policy on their next role-change or
  reset-password. To force a sync, hit `Reconcile` on the users
  tab.

### Reconcile drift

- `/api/users/reconcile` returns `matched / orphans / ghosts` per
  provider. "Orphan" = in a provider but not local. "Ghost" =
  local but not in a provider. Import orphans or unlink ghosts
  from the UI.
- A background reconciler runs every
  `RECONCILE_INTERVAL_SEC` (default 1h) and also refreshes each
  user's `last_login_at` from the providers.

### 2FA enforcement per role

- Set `require_2fa: true` on a role (e.g. `superadmin`). Any user
  whose role carries that flag must have 2FA enrolled in
  Authelia; without it, controller basic-auth denies them even
  with the correct password. Audit log records an `auth_denied`
  event.

### Audit log

- Every state-changing operation (create, delete, role change,
  password reset, invite, failed-login alert) appends a
  hash-chained entry to `<config>/controller/audit.log.jsonl`.
- The log rolls over automatically at 10 MiB. Up to 5 archives
  are kept (`audit.log.jsonl.<ts>`).
- Failed-login bursts (≥5 in 5 min, default) emit a
  `brute_force_alert` entry and start a 1-hour cooldown before
  alerting again for the same principal.

### Prometheus metrics

- `/metrics` exposes `media_stack_users_total{state}`,
  `media_stack_roles_total`, `media_stack_user_provider_up`,
  `media_stack_user_drift`, and
  `media_stack_audit_actions_total`. Scrape it like any other
  target — no extra collector needed.

### Security flags

| Env var | Default | Meaning |
|---|---|---|
| `CONTROLLER_AUTH` | `all` when password set, else `none` | `all` = every `/api/*`, `/metrics`, `/logs/*` endpoint requires auth regardless of method. `write` = legacy mode, GETs public except sensitive paths (still gated). `none` = no auth. |
| `CONTROLLER_OIDC_LOGIN_REDIRECT` | (unset) | URL to 302 browsers to when an unauth'd HTML-accepting GET arrives. Use this to delegate login to Authelia (e.g. `https://auth.media.example.com/?rd=https://controller.media.example.com`). API/JSON clients still get a plain 401. |
| `CONTROLLER_TRUSTED_PROXY_CIDRS` | (unset) | Comma-separated CIDRs of the proxy tier (e.g. Envoy pod IPs). When set, the controller accepts `Remote-User` (or the header named by `CONTROLLER_TRUSTED_PROXY_HEADER`) as an authenticated identity from requests originating inside those CIDRs — unblocks Authelia forward-auth behind Envoy without double-prompting. Requests from outside the CIDRs that carry the header are silently rejected (no spoofing). |
| `CONTROLLER_TRUSTED_PROXY_HEADER` | `Remote-User` | Header name containing the upstream-auth identity. Must match what your forward-auth emits. |
| `CSRF_ENFORCE` | (unset) | `1` = strict CSRF for every request (incl. API clients); `0` = disabled; default = smart (strict for browsers, exempt for API clients without Cookie header) |
| `STACK_ADMIN_USERNAME` / `STACK_ADMIN_PASSWORD` | env | Fallback basic-auth principal when the controller user store isn't populated. |
| `RECONCILE_INTERVAL_SEC` | `3600` | Background reconcile cadence |
| `AUTHELIA_USERS_DB` | derived | Path to Authelia `users_database.yml` for basic-auth fallback |

### Authelia forward-auth integration

When the controller sits behind Envoy with Authelia ext_authz,
you'll get a **double-prompt** (Authelia asks for the OIDC login,
Envoy forwards the request to the controller, the controller asks
for basic auth again). To fix, wire trusted-proxy:

1. Set `CONTROLLER_TRUSTED_PROXY_CIDRS` to the CIDR containing your
   Envoy pod IPs (in K8s, that's typically the pod-network CIDR; in
   compose, the docker bridge subnet).
2. In Envoy's ext_authz config, make sure Authelia's `Remote-User`
   response header is copied onto the upstream request.
3. That's it — the controller now trusts Remote-User from Envoy and
   only asks for basic auth on direct (non-proxied) access.

Security property: an attacker on the open internet who sends
`Remote-User: admin` still hits 401, because their source IP isn't
in the trusted CIDR list. Verified by the
`trusted_proxy_spoof_rejected` security-baseline test.

### Three auth paths

| Client | Path | How it looks |
|---|---|---|
| Browser (gateway + Authelia) | OIDC via Authelia → Envoy forward-auth sets `Remote-User` → controller trusts via `CONTROLLER_TRUSTED_PROXY_CIDRS`. No basic-auth prompt. | Enter OIDC creds at `auth.media.example.com`, land on dashboard. |
| Browser (direct / no gateway) | POST `/api/auth/login` with `{username, password}` → Set-Cookie `ms_session=…; HttpOnly; Secure; SameSite=Strict`. Cookie authenticates subsequent requests; revoke via POST `/api/auth/logout`. | Login form posts to `/api/auth/login`, cookie carries the session from there. |
| Programmatic client | Bearer token minted from `/api/tokens` (POST) — long-lived, revocable, scoped. | `curl -H "Authorization: Bearer <tok>" …` |

Basic auth still works as a break-glass path for curl scripts you
don't want to mint a token for.

### RBAC: `controller_admin` role flag

Authentication says who you are; `controller_admin` says what you
can do. Roles with `controller_admin: true` (the default) can call
every mutating endpoint. Roles with `controller_admin: false` get
GET-only access — dashboard loads, `/api/me` returns their profile,
but any POST/PUT/DELETE returns 403.

Shipped settings on `contracts/roles.yaml`:

- `superadmin` → `controller_admin: true`
- `family_admin`, `adult`, `teen`, `kid`, `guest` → `controller_admin: false`

Change any role's flag via Settings → Users → Roles → Edit, or
edit `contracts/roles.yaml` directly and reload.

Fallback: if the authenticating user doesn't exist in the local
store (e.g. the STACK_ADMIN env-var break-glass), mutations are
allowed. This prevents day-zero lockouts before any user has been
reconciled into the controller DB.

### API auth: basic auth vs bearer tokens

**Basic auth** (`Authorization: Basic ...`) works for dashboard and
one-off curl. Creds are the admin username + password.

**Bearer tokens** (`Authorization: Bearer ...`) are the recommended
path for programmatic clients:

- Create at Settings → Users → Tokens (or POST `/api/tokens`
  `{"name": "ci-runner", "scope": "read", "ttl_seconds": 0}`).
- Plaintext is returned **once** at creation — copy it immediately.
- Scopes: `admin` (full access) or `read` (GET-only; any mutating call
  returns 403).
- Tokens are 256-bit random, SHA-256 hashed at rest in
  `<config>/controller/api_tokens.json`. Leaking the file doesn't leak
  tokens.
- Revoke any time: POST `/api/tokens/{id}` with `{}` body → sets
  `revoked=true`. No re-verification succeeds after.
- Optional TTL: `ttl_seconds > 0` sets `expires_at`; the verifier
  rejects expired tokens.

```bash
# Mint an admin token with 30-day TTL
curl -u admin:$PW -X POST https://controller/api/tokens \
  -H 'X-CSRF-Token: $CSRF' \
  -d '{"name":"ci","scope":"admin","ttl_seconds":2592000}'
# → {"id":"...","token":"9x_...","scope":"admin", ...}

# Use the token (no basic auth needed)
curl -H "Authorization: Bearer 9x_..." https://controller/api/users

# Revoke
curl -u admin:$PW -X POST https://controller/api/tokens/<id> -d '{}'
```

### Global API hardening

- **CSRF double-submit cookie** on every mutating endpoint (not just
  `/api/users`). Smart-default: strict for browser requests (Cookie
  header present), exempt for header-less API clients.
- **Per-IP rate limit** on every POST (30 token bucket, 3/s refill).
  Separate tighter bucket (10 burst, 1/s) for `/api/users/*`, and a
  per-account slow-deliberate bucket for password reset (~1 per 20s
  per target user) to resist IP-rotation brute force.
- **Webhook SSRF block**: `/webhooks` resolves the URL and rejects
  private/loopback/link-local/multicast/reserved IPs. Defeats DNS
  rebinding by checking every address the hostname maps to.
- **Security headers** on every response: Strict-Transport-Security
  (1y), Content-Security-Policy (same-origin + `unsafe-inline` for
  inline dashboard JS), X-Frame-Options: DENY, X-Content-Type-Options:
  nosniff, Referrer-Policy: no-referrer, Permissions-Policy locked down.

## 2. End-user runbook

### Accepting an invite

- Click the `?invite=…` link the admin sent you.
- Pick a username and display name.
- Choose a password. The meter enforces: 12+ chars, at least 3
  character classes, not in the "obvious-bad" list.
- On submit, your account is created in Authelia and any
  downstream app the admin has wired up. You can log in
  immediately.

### Changing your password

1. Open the controller UI, go to **My Profile**.
2. Current password → new password → confirm.
3. The request hits `/api/me/password`; we verify the old
   password against Authelia before accepting the new one.
4. The new password is pushed to every app that supports it.

### Listing your active sessions

- **My Profile → Sessions** lists every live session the
  controller can see across providers. Click **Revoke** on one
  to terminate a specific session, or **Revoke all** to boot
  yourself everywhere (you'll have to log back in).

### Forgot password

- No self-service reset; contact your admin, they'll either
  reset directly or issue a new invite.

## 3. Troubleshooting

| Symptom | Check |
|---|---|
| "CSRF token missing or invalid" on every POST | A stale dashboard tab still has the old cookie. Force-refresh the page. |
| User created but not in Jellyfin | Check `/api/users/reconcile` for ghosts. The provider may have been unhealthy when the user was created; hit the user's row → Retry provision. |
| Password reset rejected as "too short" | The policy requires 12+ chars and 3 character classes. Adjust or wait for admin to soften `PasswordPolicy`. |
| `brute_force_alert` flooding the audit log | Check the originating IP in the alert detail — usually a misconfigured script hammering `/api/users/*/reset-password`. |
| `/metrics` returns 500 | Check controller logs; most common cause is an unhealthy provider throwing during `provider_health()`. |
