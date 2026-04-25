# Session Visibility & Security Reporting — Follow-up Roadmap

Items intentionally deferred out of the v1 session-visibility feature.
v1 scope is tracked in the main feature plan; this file records what we
chose not to ship initially, **why**, and what it would take to pick
each one up later.

Status: each item is `flagged` (agreed to defer) until someone opens a
tracking ticket. When work begins, move the item into the main docs
tree (most likely `docs/security.md` or `docs/user-management.md`) and
link the ticket here.

---

## 1. "Sign in as" (admin impersonation)

**What it is.** An admin clicks a button on a user row and is issued a
short-lived session as that user, so they can reproduce a support
issue without asking for the user's password.

**Why deferred.** Every impersonation feature that ships without a
rigid audit envelope turns into a backdoor. A correct design needs:

- Separate impersonation session type (never mintable by password).
- Hard time cap (≤ 15 min, no extension).
- Admin's real identity stamped on every downstream action.
- Blocked on the impersonated account after N concurrent impersonations.
- User-visible "you were impersonated by X on Y" entry in their own
  security feed.
- Tripwire: impersonated session *cannot* change the target's password,
  MFA, or email.

**Minimum viable build.**
1. New session kind `impersonation` in `session_store.py` with a
   distinct TTL field and an `acting_as` + `acting_for` pair.
2. Audit action `impersonation_started` / `impersonation_ended` with
   both identities.
3. Authorization matrix update: impersonation sessions cannot call any
   endpoint tagged `sensitive_self`.
4. UI affordance on the Users tab + a persistent banner in the
   impersonated session.

**Dependencies.** v1 session-binding (item 7) and the event bus (item
12) must be in place first; otherwise the tripwires can't fire.

---

## 2. Device approval workflow

**What it is.** When a user logs in from a device that has never been
seen before, the session is created in a `pending_device_approval`
state. Until an admin (or the user themselves, from a trusted device)
approves the device, it can authenticate but not access protected
media / admin functions.

**Why deferred.** Heavy UX: approval inbox, mobile push for
self-approval, device naming, revocation flows. Also requires a
durable device-identity primitive (signed device token in a
long-lived cookie) that v1 does not build.

**Minimum viable build.**
1. Durable device identity: signed `device_id` cookie, 1-year TTL,
   bound to a server-side device record with first-seen, last-seen,
   approved-by, approved-at.
2. Session creation path inspects device state; issues
   `pending_device_approval` session if unknown.
3. Gateway middleware denies protected routes for pending sessions
   with a redirect to a waiting-room page.
4. Admin + self approval endpoints, both audited.
5. Email/webhook notification to user on new-device login (already
   present from v1 item 3).

**Dependencies.** v1 UA/device classifier gets us most of the way to
device *description*; what's missing is the *identity* cookie.

**Interaction with Authelia.** Since Authelia can be configured to
require 2FA on unknown devices, the MVP here could be as simple as
"lean on Authelia's rememebered-device feature + surface its state in
our UI", deferring our own device store entirely. Worth evaluating
before committing to a full build.

---

## 3. GDPR-compliant purge with chain integrity

**What it is.** A legally-mandated "forget this user's PII" operation
that removes identifying data (IPs, user agents, usernames) from the
audit log without breaking its hash chain.

**Why deferred.** Fundamental conflict: hash-chained audit logs treat
every byte as evidence. Any edit invalidates the chain from that
point forward. Real solutions exist but none are trivial.

**Design options.**

- **Per-entry redaction marker.** Keep the entry slot, keep its hash,
  but replace the payload with a `redacted: {reason, ts}` stub. The
  hash stays valid because the hash was computed over the original
  payload; the payload is just no longer retrievable. Downside: an
  auditor can't independently verify the chain because they can no
  longer recompute the hashes of redacted entries. Mitigation: store a
  Merkle proof per redaction.
- **Dual logs.** `audit.jsonl` is the authoritative hash-chained log
  with pseudonymous user IDs. A separate `identity.jsonl` maps IDs to
  PII and is purged freely. Chain integrity preserved. Cost: joining
  across two logs at query time.
- **Short retention.** Keep the full chain for 30 days, then archive
  with identity stripped. Cheap but loses forensic depth.

**Minimum viable build.** The dual-log design is probably the right
answer. Requires:

1. Pseudonymous user ID (stable UUID) minted at user creation.
2. `identity.jsonl` with `{uuid, username, email, ips_seen, ...}`,
   compacted periodically.
3. Audit log emits only UUIDs.
4. `POST /api/users/{id}/purge-identity` redacts `identity.jsonl`
   entries for that UUID; audit log remains intact.

**Dependencies.** Touches every audit call site. Do not retrofit into
v1 — design it, then migrate.

**Legal note.** If the deployment is a home-lab / family install,
GDPR likely does not apply. If it's ever commercial or handling
EU-resident data, this is mandatory.

---

## 4. Compromised-password check (HIBP k-anonymity)

**What it is.** On password set / change, hash the new password with
SHA-1, send the first 5 hex chars of the hash to
`api.pwnedpasswords.com`, receive back a list of matching hash
suffixes, check locally. Reject (or warn) if the password appears in
breach corpora.

**Why deferred.** Trivial to build (~30 LOC), but adds a runtime
network dependency to the password-change path that the rest of the
stack does not require. Needs an explicit opt-in config key and a
graceful-degradation story for offline deployments.

**Minimum viable build.**
1. `CONTROLLER_HIBP_CHECK=on|warn|off`, default `warn`.
2. `core/auth/password_breach_check.py` with a 5-second timeout
   and a `BreachCheckResult` dataclass.
3. Wire into `/api/password-reset` and `/api/users/{id}/set-password`
   with behavior keyed to the config.
4. Audit entry gains `breach_check_result` field (`unchecked`,
   `clean`, `breached:{count}`, `error`).
5. Offline mode: ship an optional local bloom-filter database as a
   fallback. (HIBP publishes the full corpus for download.)

**Dependencies.** None. Can be shipped as a standalone patch any time.

---

## Misc items parked here for visibility

These came up during v1 scoping but aren't big enough to warrant a
full section. Each needs a short design note before work starts.

- **Risk score per user** — roll up signals (failure rate, new
  locations, concurrent count, admin role) into a 0–100 number shown
  on the Users tab. Useful for ranking review work. Needs a scoring
  policy doc.
- **Signed share links for support** — admin can generate a signed
  short-TTL URL that carries support context. Security-sensitive;
  design carefully.
- **CAPTCHA escalation** — after N login failures, the next attempt
  must pass a CAPTCHA before being evaluated. Requires picking a
  provider (hcaptcha, friendly-captcha) and handling the offline
  case.
- **Auto-lockout policies with escalation** — `3 failures → CAPTCHA,
  5 → cooldown, 10 → lockout, 15 → admin review`. The primitives
  exist in v1 (failed-login tracker, ban store); a policy engine
  does not.
- **Session labels / nicknames** — user can rename "Unknown device
  (Chrome on Linux)" to "My work laptop". Small, nice-to-have.
- **Syslog / SIEM forwarder** — the audit log is already JSONL; an
  opt-in tail-and-forward daemon is a small add.

---

## Jellyfin local-password decision (deferred)

**Date**: 2026-04-24 · **Context**: after the `probe_credentials` /
`probe_password_propagation` split, the new propagation check will
report `jellyfin: not_propagated` on our deployment because the local
`admin` row in Jellyfin has `HasPassword=false` — users sign in via
Authelia SSO → OIDC.

**Decision to make**: do we

- (A) Run the admin-reset flow once so the local password is set,
  giving us a break-glass path if Authelia is down, OR
- (B) Accept `not_propagated` as the steady state and suppress the
  alert in the Security tab, documenting "OIDC-only, no local
  password by design"?

**Trade-off**: (A) gains an offline fallback at the cost of a secret
to rotate forever and another credential surface to leak. (B)
simplifies the credential story but means an Authelia outage locks
everyone out including ops.

Keep the question open until the security contract's break-glass
policy is written — at which point either choice becomes a one-line
run.

---

## Review cadence

Re-read this file when planning the release after v1 ships. If an
item has been untouched for two releases, re-evaluate whether it's
still relevant or whether the underlying need has been solved by
Authelia upgrades or other stack changes.
