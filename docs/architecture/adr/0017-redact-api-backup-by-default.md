# ADR-0017 — Redact `/api/backup` cleartext output by default

**Status:** Proposed (2026-05-12). Direct response to the
2026-05-12 secrets-in-history incident (see CHANGELOG v1.0.368 +
`tests/unit/ratchets/test_no_committed_secrets_ratchet.py`'s
incident note).

Authors: matthew

## Context

`GET /api/backup` ([_diagnostics.py:67-117](../../../src/media_stack/api/services/config/_diagnostics.py#L67-L117))
serializes a full configuration snapshot for operator export.
The current payload includes:

* Every `*_API_KEY` env var in cleartext — the response computes
  `api_keys_masked` (first 8 chars + `…`) but ships the un-masked
  `api_keys` dict alongside it. Operators who hand-rolled an
  `/api/backup` save got a JSON with both fields.
* The full raw `profile.yaml` text (`backup["profile_raw"]`) —
  may contain the Google OAuth `client_secret`, OIDC tokens,
  webhook URLs with embedded keys.
* The full text of every service config file declared in the
  service registry's `api_key_config` / `password_config` fields:
  Bazarr's `config.yaml` (which carries
  `flask_secret_key` + Plex `encryption_key` + every connected
  *arr's apikey), Authelia's `configuration.yml`,
  qBittorrent's `qBittorrent.conf`, etc.
* The serialised `ControllerState.to_dict()` payload (admin
  session data, action history, recent error envelopes that may
  embed redirect URLs with bearer tokens).

The endpoint is gated behind admin auth, so the threat model is
"a read-scope-admin token must not be enough to exfiltrate every
provider credential" — exactly the same threat model
`test_no_secret_in_api_responses_ratchet` enforces against the
GET `/api/keys` endpoint.

The endpoint's cleartext output is what caused the 2026-05-12
incident: an operator hit `/api/backup`, captured the JSON, and
committed it as `tests/fixtures/api_responses/backup.json`. Eight
production secrets (Google OAuth pair, Authelia storage
encryption key, Bazarr `flask_secret_key` + Plex
`encryption_key`, three *arr API keys) ended up in git history.

## Decision

Make `/api/backup` redact by default, with explicit opt-in for
the cleartext path. Three changes:

### 1. Redact at the response boundary, not the source

The backup payload's structure is correct (one envelope, one
serializer); the fix is to add a redactor that walks the
serialised dict before
`json.dumps(...).encode("utf-8")`. Reuse
`core.auth.secret_redaction` — the same module
`test_no_secret_in_api_responses_ratchet` already enforces
against for the GET `/api/keys` path.

Redactor surface:

* `api_keys` dict — every value becomes `REDACTED-<key>` (the
  existing `api_keys_masked` field stays untouched as the
  shape the dashboard already consumes).
* `service_configs` map — for every entry that's a YAML / JSON
  / INI / XML blob, parse it, walk for sensitive-shaped keys
  (`api_key`, `apikey`, `password`, `client_secret`,
  `encryption_key`, `flask_secret_key`, `session_key`,
  `private_key`, `token`, `passkey`, `secret`) and replace the
  values with `REDACTED-<original-key>`. Falls back to a regex
  sweep over the raw text if parsing fails.
* `profile_raw` — same parse-then-walk; YAML structure is
  predictable enough.
* `state.to_dict()` payload — passes through
  `core.auth.secret_redaction.redact_secrets` (already exists
  and is the same path the audit log writer uses).

### 2. Explicit opt-in for cleartext via additional scope

A new query param `?include_secrets=1` ONLY honoured when:

* The caller's token has the `BackupExportCleartext` scope
  (new — adds to `core.auth.scopes.PROTECTED_SCOPES`, gated by
  the same admin-issuance flow as the existing `Admin` scope).
* The request's `X-Confirm-Cleartext-Backup` header equals
  `yes-I-know-the-risk` (literal string — explicit-action
  guard so curl-as-bearer-token can't quietly trip it).

Both gates required. The cleartext path is for emergency
forensics + offline-restore use cases; everyday operator
exports stay redacted by default.

The dashboard's "Export config" button calls the redacted
path. There's no UI affordance for the cleartext path —
intentionally. Operators who need it can use curl with the
header.

### 3. Restore-side: accept both shapes

`POST /api/backup/restore` ([_diagnostics.py:119](../../../src/media_stack/api/services/config/_diagnostics.py#L119))
needs to keep accepting cleartext backups (people have old
backups already taken under the cleartext shape). For redacted
backups, the restore handler:

* Detects redacted markers (`REDACTED-*` values).
* Refuses the restore if every secret is redacted AND no
  out-of-band replacement provided — without secrets the
  restore would produce a broken stack.
* Accepts a side-channel `?secrets_from=k8s-secret` /
  `?secrets_from=env` option that re-injects each redacted
  value from the current cluster's `media-stack-secrets` /
  `os.environ` (the source of truth post-bootstrap anyway).

Refusal vs. partial-restore is the right posture — a quiet
broken stack is worse than a loud refusal.

## Phases

| Phase | Status | Notes |
|---|---|---|
| Phase 1 — backup redactor + scope + header guard | not started | The 90% fix. Existing `core.auth.secret_redaction` covers state.to_dict(); the new code is the service-config walker. |
| Phase 2 — restore-side `secrets_from` re-injection | not started | Pulls from k8s secret + env at restore time. Refuses if neither has the values. |
| Phase 3 — retire the old `api_keys` cleartext field | not started | Backward-compat: keep emitting `api_keys` for ~3 releases with values replaced by `REDACTED-…` so consumers (the dashboard, any operator scripts) keep working; then drop the field entirely once the dashboard's `api_keys_masked` consumer is the only reader. |
| Phase 4 — ratchet: `tests/unit/ratchets/test_no_secret_in_api_responses_ratchet.py` extends to GET `/api/backup` | not started | Pin the redaction at the AST layer so a future refactor can't silently re-introduce the leak. |

## What this ADR does NOT propose

* **A separate "lite" backup endpoint** that ships only the
  parsed-state envelope without service configs. Considered;
  rejected because the value of `/api/backup` is the full
  bundle — operators reach for it precisely when they want
  "everything for restore". Splitting it into two endpoints
  fragments the operator story.
* **Encryption-at-rest of the backup payload.** That's a
  separate concern (transport security between the controller
  and the operator's browser). The redact-by-default approach
  is orthogonal — useful even when transport is TLS-protected,
  because the captured JSON gets committed to git or pasted
  into a chat support thread.
* **Disabling `/api/backup` entirely for non-`internet_exposed`
  deploys.** The endpoint is useful even on LAN-only
  installations (operator-driven backups, disaster recovery).
  The redact-by-default posture works regardless of exposure
  level.

## Cross-references

* CHANGELOG `v1.0.368` — incident write-up.
* `tests/unit/ratchets/test_no_committed_secrets_ratchet.py` —
  catches a committed leak post-hoc; this ADR closes the
  upstream cause.
* `tests/unit/ratchets/test_no_secret_in_api_responses_ratchet.py`
  — Phase 4 of this ADR extends that ratchet to cover
  `/api/backup`.
* `core/auth/secret_redaction.py` — the redactor module the
  Phase 1 walker delegates to.
* `CONTRIBUTING.md` "What NOT to commit" — operator-side
  guidance referencing this ADR as the long-term fix.

---

**Project Steward**
Matthew Loschiavo · [matthewloschiavo.com](https://matthewloschiavo.com) · [mploschiavo@gmail.com](mailto:mploschiavo@gmail.com)
