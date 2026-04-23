# Security Roadmap

Open security work that hasn't shipped yet. Anything here is **not** part of the current [security baseline](../security.md) — promote to that doc once it's coded and the live test passes.

## Open items

### `body_size_cap` — currently failing for the controller

Oversized request body (2 MiB) should be rejected with 400/413 instead of buffered into memory. The check exists in the harness but the controller does not yet enforce a request-body cap, so the live baseline reports a failure on this check. Blocks DoS via memory exhaustion.

## Implemented but not in the live baseline

These are coded and tested elsewhere; they're listed here so the gap between "shipped behavior" and "what the live security baseline measures" stays visible.

| Check | Status | Notes |
|---|---|---|
| `ip_lockout_on_brute_force` | **Implemented** | Covered by [tests/unit/test_ip_lockout.py](../../tests/unit/test_ip_lockout.py) rather than the live baseline (a live test would lock the audit runner's own IP out of the whole suite). 20 failed-auth attempts within 5 minutes from one IP → 15-minute 429 lockout. |
| `origin_header_check` | **Implemented as check 8b** in the live baseline (`cross_origin_mutation_rejected`). |

## Not yet coded

Each item below is a planned check. When implemented, the corresponding test moves into `tests/security/` and the row migrates from this roadmap into the [security baseline](../security.md) matrix.

| Check | Meaning |
|---|---|
| `content_type_enforcement` | `/api/*` POSTs with `Content-Type` ≠ `application/json` are rejected. |
| `audit_log_covers_mutation` | Every successful mutation writes an audit entry (requires read access to the audit log via `/api/audit-log`). |
| `tls_enforced` | If `X-Forwarded-Proto=http` arrives via a trusted proxy, the service redirects or refuses. |
| `cors_headers_absent` | Wildcard `Access-Control-Allow-Origin: *` is not set on any response. |
| `options_preflight_sane` | `OPTIONS /api/users` returns a strict CORS preflight (or 405 if unused). |

## Promotion checklist

When a roadmap item ships:

1. Add the check to `tests/security/security_audit.py`.
2. Verify it runs in the per-service suites that use it.
3. Update the live pass/fail matrix in [security.md](../security.md).
4. Remove the row from this roadmap.

If the check is implemented but isn't suitable for the live baseline (like `ip_lockout_on_brute_force`), add an explicit row to "Implemented but not in the live baseline" with the reason.
