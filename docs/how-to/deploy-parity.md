# Deploy Parity — docker-compose + kubernetes

The media-automation-stack ships on two targets, **docker-compose** and
**kubernetes**. Every feature that ships must behave identically in
both environments. This document records the invariants that keep
that true.

## Why parity matters

Two deploy targets sound like 2× the surface area, but in practice
it's less — most of our code runs inside containers that look the
same on both platforms. The divergence is at the **edges**: what
filesystem the config lives on, how services talk to each other,
how secrets are injected, and how the network isolates.

If those edge-differences leak into the application code, we end up
with features that "work in compose but silently no-op in k8s"
(or the reverse). That's worse than a feature that doesn't ship at
all, because nobody knows until someone files a bug six months later.

The parity contract: **every application decision must compile down
to identical observable behaviour across targets**. Where it can't,
the divergence is documented here.

## Invariant 1 — config root is always `${CONFIG_ROOT}`

Both targets mount a writable directory at a single path the
application cares about:

| Target | Host path | Mount point |
|---|---|---|
| compose | `../config` (parent-of-compose-file) | `/srv-config` |
| k8s | PVC `media-stack-config-controller` | `/srv-config` |

The `CONFIG_ROOT` env var points at the mount. Every writable file
the controller owns lives under it:

- `${CONFIG_ROOT}/controller/users.json` — local user store
- `${CONFIG_ROOT}/controller/audit.jsonl` — hash-chained audit log
- `${CONFIG_ROOT}/controller/bans.json` — user + IP ban store
- `${CONFIG_ROOT}/controller/api_tokens.json` — bearer token store
- `${CONFIG_ROOT}/controller/runtime-config.json` — live config
- `${CONFIG_ROOT}/authelia/users_database.yml` — Authelia users
- `${CONFIG_ROOT}/authelia/configuration.yml` — Authelia config
- `${CONFIG_ROOT}/authelia/db.sqlite3` — Authelia MFA / session DB

Because the mount is identical, the session-visibility feature's
**BanStore**, **audit log**, and **password-ticket store** work
identically on both targets with zero deploy-target branches in
the code. The `AutheliaIPDenyProvider` reads + writes
`configuration.yml` via `SafeYamlEditor`, and Authelia picks up the
reload via its watch (users_database.yml) or a SIGHUP
(configuration.yml).

## Invariant 2 — gateway is Envoy, authz is Authelia ext_authz

Both targets put **Envoy** in front of every app with **Authelia**
wired in via `ext_authz`. Requests to any app first hit Envoy,
Envoy calls Authelia to decide allow/deny, Authelia checks:

1. User is authenticated (session cookie, basic auth, OIDC).
2. User's role has access to the requested domain.
3. Client IP is not in Authelia's `access_control.rules` deny list
   (this is where our `AutheliaIPDenyProvider` merges its managed
   rule).

Because the enforcement is at the edge, the same ban lists and
auth model apply regardless of whether the backend is a docker
container or a k8s pod.

## Invariant 3 — secrets are env, files, or k8s Secrets — never both

A secret is set **either** via an env var **or** via a file under
`${CONFIG_ROOT}` **or** via a k8s Secret. Never all three. The
controller reads whichever is configured and errors loudly if
two sources disagree.

The k8s Secret surface is a super-set of the env surface — every
env-driven secret can instead come from a Secret without any
code change. The compose target never sees Secrets; the k8s
target can use either.

## Invariant 4 — logs go to stdout

Both targets capture container stdout via the runtime
(docker log driver / kubelet). No application code writes to a
log file directly. This means structured audit entries go to
`${CONFIG_ROOT}/controller/audit.jsonl` (persistent, chain-hashed)
and operational logs go to stdout (ephemeral, JSON-shaped for
easy ingestion).

## Invariant 5 — health probes use the same endpoints

Every service exposes `/healthz` (cheap liveness) and `/readyz`
(deeper readiness). Compose health checks + k8s probes both hit
these paths. The security-visibility health hook re-uses the
same endpoints.

## Session-visibility parity specifics

| Feature | Compose | k8s | Same? |
|---|---|---|---|
| BanStore atomic write | SafeJsonEditor → tmp file → rename on tmpfs? | Same, on PVC | ✅ |
| Authelia IP deny rule merge | Writes to host-mounted config.yml | Writes to PVC-mounted config.yml | ✅ |
| Audit log hash chain | appends to CONFIG_ROOT/controller/audit.jsonl | same PVC path | ✅ |
| MFA state read | reads Authelia's db.sqlite3 at shared mount | same PVC | ✅ |
| Emergency revoke | fans out to Envoy + providers | same | ✅ |
| Prometheus metrics | `/metrics` scraped by Prometheus container | scraped by Prometheus Operator | ✅ |
| Webhook SSRF guard | blocks RFC-1918 | also blocks `*.svc` / `*.svc.cluster.local` | ✅ (CIDR list differs, but both block the hostile cases) |
| Notification webhook URL validation | rejects `localhost`, `host.docker.internal`, private ranges | rejects `localhost`, k8s service DNS | ✅ |

## Cases where parity breaks (documented, deliberate)

1. **Multi-replica controller** — k8s supports > 1 replica of the
   controller, compose does not. When running multi-replica,
   `SessionStore`, `LoginHistoryIndex`, and the password-ticket
   store (all currently in-process memory) must be externalised
   (e.g. to Redis). Today we support 1 replica only on both
   targets; the roadmap is
   [session-visibility-followups.md](../roadmap/session-visibility-followups.md).

2. **Authelia reload signal** — on compose, `AutheliaIPDenyProvider`'s
   `reload_hook` restarts the `authelia` container via
   `admin_svc.restart_service("authelia")`. On k8s, the hook
   `kubectl rollout restart deployment/authelia` (or equivalent
   via the k8s Python client). The CODE is the same — the hook
   callable differs at wiring time. This is the only place
   where deploy-target branching lives.

3. **TLS** — compose uses a self-signed cert auto-minted by
   Envoy; k8s typically uses cert-manager + a real CA issuer.
   Transparent to the application.

## Parity tests

- `tests/security/test_controller_security_baseline.py` runs against
  either target.
- `tests/unit/test_pluggable_authelia_ratchet.py` prevents
  hard-coded Authelia imports, so swapping to Authentik works
  on both.
- `tests/unit/test_security_headers_ratchet.py` pins headers that
  must be present regardless of target.
