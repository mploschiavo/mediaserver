"""Named action constants for ``AuditLog``.

Every string written to ``AuditEntry.action`` should come from this
module. Consumers filter via constants instead of hardcoded strings,
so a rename is a single-file change, all filter lists stay
discoverable via ``grep`` for the constant name, and the UI's action
picker is generated from ``ALL`` — no drift between server and
client.

Groups
------
- ``ACCOUNT_MGMT`` — user/invite CRUD (historical).
- ``AUTH_EVENTS`` — login success/failure/blocked/rate_limited/logout.
- ``SESSION_MGMT`` — per-session revoke, revoke-all.
- ``PASSWORD_EVENTS`` — change + reset.
- ``BAN_EVENTS`` — user + IP ban add/remove.
- ``ANOMALY_EVENTS`` — detected-but-not-blocked signals (new location,
  impossible-travel, concurrent-session spike).

Each group is a ``frozenset`` so consumers can do
``action in AUTH_EVENTS`` without importing every constant.
``ALL`` is the union of every group; the UI action filter picks from
it so operators don't type free-form strings that never match.
"""

from __future__ import annotations

# ---- Account management (existing flows) -------------------------------

CREATE_USER = "create_user"
CREATE_USER_VIA_ADOPTION = "create_user_via_adoption"
DELETE_USER = "delete_user"
SET_ROLE = "set_role"
SET_STATE = "set_state"
INVITE_CREATED = "invite_created"
INVITE_ACCEPTED = "invite_accepted"
INVITE_REVOKED = "invite_revoked"
IMPORT_ORPHAN = "import_orphan"
RELINK_ORPHAN = "relink_orphan"
UNLINK_GHOST = "unlink_ghost"

ACCOUNT_MGMT: frozenset[str] = frozenset({
    CREATE_USER,
    CREATE_USER_VIA_ADOPTION,
    DELETE_USER,
    SET_ROLE,
    SET_STATE,
    INVITE_CREATED,
    INVITE_ACCEPTED,
    INVITE_REVOKED,
    IMPORT_ORPHAN,
    RELINK_ORPHAN,
    UNLINK_GHOST,
})

# ---- Authentication events (added for session visibility) --------------

LOGIN_SUCCESS = "login_success"
LOGIN_FAILURE = "login_failure"
LOGIN_BLOCKED = "login_blocked"
LOGIN_RATE_LIMITED = "login_rate_limited"
LOGOUT = "logout"

AUTH_EVENTS: frozenset[str] = frozenset({
    LOGIN_SUCCESS,
    LOGIN_FAILURE,
    LOGIN_BLOCKED,
    LOGIN_RATE_LIMITED,
    LOGOUT,
})

# ---- Session management -----------------------------------------------

SESSION_REVOKED = "session_revoked"
REVOKE_SESSIONS = "revoke_sessions"
EMERGENCY_REVOKE_ALL = "emergency_revoke_all"

SESSION_MGMT: frozenset[str] = frozenset({
    SESSION_REVOKED,
    REVOKE_SESSIONS,
    EMERGENCY_REVOKE_ALL,
})

# ---- Password events --------------------------------------------------

PASSWORD_CHANGE = "password_change"
RESET_PASSWORD = "reset_password"
PASSWORD_TICKET_CONSUMED = "password_ticket_consumed"

PASSWORD_EVENTS: frozenset[str] = frozenset({
    PASSWORD_CHANGE,
    RESET_PASSWORD,
    PASSWORD_TICKET_CONSUMED,
})

# ---- Ban events -------------------------------------------------------

BAN_USER_ADD = "ban_user_add"
BAN_USER_REMOVE = "ban_user_remove"
BAN_IP_ADD = "ban_ip_add"
BAN_IP_REMOVE = "ban_ip_remove"

BAN_EVENTS: frozenset[str] = frozenset({
    BAN_USER_ADD,
    BAN_USER_REMOVE,
    BAN_IP_ADD,
    BAN_IP_REMOVE,
})

# ---- Anomaly / detection signals --------------------------------------

ANOMALY_NEW_LOCATION = "anomaly_new_location"
ANOMALY_IMPOSSIBLE_TRAVEL = "anomaly_impossible_travel"
ANOMALY_CONCURRENT_SPIKE = "anomaly_concurrent_spike"
ANOMALY_CREDENTIAL_STUFFING = "anomaly_credential_stuffing"

ANOMALY_EVENTS: frozenset[str] = frozenset({
    ANOMALY_NEW_LOCATION,
    ANOMALY_IMPOSSIBLE_TRAVEL,
    ANOMALY_CONCURRENT_SPIKE,
    ANOMALY_CREDENTIAL_STUFFING,
})

# ---- Media-integrity actions ----------------------------------------

MEDIA_INTEGRITY_CONFIG_ENFORCED = "media_integrity_config_enforced"
MEDIA_INTEGRITY_CONFIG_ENFORCE_FAILED = "media_integrity_config_enforce_failed"
MEDIA_INTEGRITY_DUPLICATE_RESOLVED = "media_integrity_duplicate_resolved"
MEDIA_INTEGRITY_DUPLICATE_REVIEW_NEEDED = "media_integrity_duplicate_review_needed"
MEDIA_INTEGRITY_RECONCILE_FAILED = "media_integrity_reconcile_failed"

MEDIA_INTEGRITY_EVENTS: frozenset[str] = frozenset({
    MEDIA_INTEGRITY_CONFIG_ENFORCED,
    MEDIA_INTEGRITY_CONFIG_ENFORCE_FAILED,
    MEDIA_INTEGRITY_DUPLICATE_RESOLVED,
    MEDIA_INTEGRITY_DUPLICATE_REVIEW_NEEDED,
    MEDIA_INTEGRITY_RECONCILE_FAILED,
})

# ---- Union of every tracked action -----------------------------------

ALL: frozenset[str] = (
    ACCOUNT_MGMT
    | AUTH_EVENTS
    | SESSION_MGMT
    | PASSWORD_EVENTS
    | BAN_EVENTS
    | ANOMALY_EVENTS
    | MEDIA_INTEGRITY_EVENTS
)


__all__ = [
    "ACCOUNT_MGMT",
    "ALL",
    "ANOMALY_CONCURRENT_SPIKE",
    "ANOMALY_CREDENTIAL_STUFFING",
    "ANOMALY_EVENTS",
    "ANOMALY_IMPOSSIBLE_TRAVEL",
    "ANOMALY_NEW_LOCATION",
    "AUTH_EVENTS",
    "BAN_EVENTS",
    "BAN_IP_ADD",
    "BAN_IP_REMOVE",
    "BAN_USER_ADD",
    "BAN_USER_REMOVE",
    "CREATE_USER",
    "CREATE_USER_VIA_ADOPTION",
    "DELETE_USER",
    "EMERGENCY_REVOKE_ALL",
    "IMPORT_ORPHAN",
    "INVITE_ACCEPTED",
    "INVITE_CREATED",
    "INVITE_REVOKED",
    "LOGIN_BLOCKED",
    "LOGIN_FAILURE",
    "LOGIN_RATE_LIMITED",
    "LOGIN_SUCCESS",
    "LOGOUT",
    "MEDIA_INTEGRITY_CONFIG_ENFORCED",
    "MEDIA_INTEGRITY_CONFIG_ENFORCE_FAILED",
    "MEDIA_INTEGRITY_DUPLICATE_RESOLVED",
    "MEDIA_INTEGRITY_DUPLICATE_REVIEW_NEEDED",
    "MEDIA_INTEGRITY_EVENTS",
    "MEDIA_INTEGRITY_RECONCILE_FAILED",
    "PASSWORD_CHANGE",
    "PASSWORD_EVENTS",
    "PASSWORD_TICKET_CONSUMED",
    "RELINK_ORPHAN",
    "RESET_PASSWORD",
    "REVOKE_SESSIONS",
    "SESSION_MGMT",
    "SESSION_REVOKED",
    "SET_ROLE",
    "SET_STATE",
    "UNLINK_GHOST",
]
