"""Metric-name contract for the session-visibility feature.

Every Prometheus metric the session-visibility feature exposes is
declared here as a module-level constant. Other modules MUST import
these constants rather than hard-coding string literals, so a name
change is a single-point edit and grep/IDE rename works end-to-end.

This file is documentation-as-code: each constant is accompanied by a
comment describing what increments it, when it moves, and what labels
it carries. If you add a metric here, document it the same way.

Naming follows Prometheus conventions:
  * lowercase with underscores,
  * ``_total`` suffix for monotonic counters,
  * ``_seconds`` / other unit suffix for gauges with natural units,
  * otherwise a plain descriptive noun (e.g. ``sessions_active``).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Gauges
# ---------------------------------------------------------------------------

# Current count of active sessions broken down by the provider that
# minted them (authelia, local, etc.). Moves up on login, down on
# logout / session revocation / expiry.
# Labels: provider
SESSIONS_ACTIVE = "sessions_active"

# Current count of active bans broken down by ban kind (user, ip, token).
# Moves up when a ban is applied, down when it expires or is lifted.
# Labels: kind
BANS_CURRENT = "bans_current"

# Age, in seconds, of the most recent audit-chain head entry. A large
# value here means the audit chain has not advanced recently, which is
# usually a signal that the writer is stuck. No labels — there is only
# one audit chain per process.
# Labels: (none)
AUDIT_CHAIN_HEAD_AGE_SECONDS = "audit_chain_head_age_seconds"

# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------

# Total login failures, classified by rejection reason
# (bad_password, unknown_user, locked, 2fa_failed, ...).
# Labels: reason
LOGIN_FAILURES_TOTAL = "login_failures_total"

# Total successful logins, broken down by provider.
# Labels: provider
LOGIN_SUCCESSES_TOTAL = "login_successes_total"

# Total bans applied since process start, by ban kind.
# Labels: kind
BAN_APPLIED_TOTAL = "ban_applied_total"

# Total sessions revoked since process start. ``reason`` values are
# free-form (admin_revoke, password_change, idle_timeout, ...).
# Labels: reason
SESSION_REVOKED_TOTAL = "session_revoked_total"

# Total password changes since process start. ``self_change`` is the
# string ``"true"`` if the user changed their own password, ``"false"``
# if an admin changed it for them.
# Labels: self_change
PASSWORD_CHANGED_TOTAL = "password_changed_total"

# Total anomalies the anomaly-detection subsystem has raised. ``kind``
# identifies the detector (impossible_travel, burst_login, ...).
# Labels: kind
ANOMALY_DETECTED_TOTAL = "anomaly_detected_total"


# Aggregate iterable so tests can assert the full contract in one place
# and downstream code can bulk-register. Keep this list exhaustive.
ALL_METRIC_NAMES: tuple[str, ...] = (
    SESSIONS_ACTIVE,
    BANS_CURRENT,
    AUDIT_CHAIN_HEAD_AGE_SECONDS,
    LOGIN_FAILURES_TOTAL,
    LOGIN_SUCCESSES_TOTAL,
    BAN_APPLIED_TOTAL,
    SESSION_REVOKED_TOTAL,
    PASSWORD_CHANGED_TOTAL,
    ANOMALY_DETECTED_TOTAL,
)
