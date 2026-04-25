"""Session-admin + MFA-state reader for Authelia backed by its sqlite DB.

Authelia's file backend persists TOTP configurations, WebAuthn
credentials, and authentication logs in ``db.sqlite3``. This module
reads those tables so the controller can report **real** MFA-enrollment
state per user (instead of the conservative ``MFAState.none()`` that
the users-database-only path returns from ``AutheliaFileProvider``).

What this module does NOT do
----------------------------

- Enumerate live HTTP sessions. Authelia with the default file
  backend uses encrypted session cookies; the authoritative "which
  cookies are valid right now" lives in Authelia's memory, not on
  disk. Deployments that need live session enumeration must switch
  Authelia to a Redis-backed session store — out of scope here.
- Modify sqlite state. We open the database in ``mode=ro`` so
  concurrent writes from Authelia can't race us. User bans still go
  through ``AutheliaFileProvider.disable_user`` (which edits
  ``users_database.yml``, a file Authelia watches).

Schema drift
------------

Table names have moved between Authelia versions. The WebAuthn table
was ``webauthn_devices`` on 4.37-and-earlier and
``webauthn_credentials`` on 4.38+. We probe both names and degrade
gracefully if neither exists (bootstrap deployments don't have these
tables until a user has enrolled anything).

Only the columns we read are assumed; SELECT is narrow. If Authelia
drops one of those columns in a future version, the query raises
``sqlite3.OperationalError`` with "no such column" — we log and
return the conservative empty result rather than crash the request.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from media_stack.core.auth.users.visibility_protocols import MFAState

_log = logging.getLogger("media_stack")

_TOTP_TABLE = "totp_configurations"
_WEBAUTHN_TABLE_CANDIDATES = ("webauthn_credentials", "webauthn_devices")
_AUTH_LOG_TABLE = "authentication_logs"

_SQLITE_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class _TOTPInfo:
    created_at: str
    last_used_at: str


class AutheliaSessionAdmin:
    """Read-only MFA + authentication-history reader for Authelia.

    ``db_path`` is the full path to Authelia's ``db.sqlite3``
    (typically ``/config/db.sqlite3`` inside the Authelia container,
    mounted from the host's ``/srv-config/authelia``).

    A fresh connection is opened per query because Authelia itself is
    writing concurrently — a persistent reader connection risks lock
    contention and isn't worth it for the low query rate this reader
    receives.
    """

    name = "authelia"

    def __init__(self, db_path: Path) -> None:
        self._path = Path(db_path)

    # ---- MFAStateProvider -----------------------------------------------

    def mfa_state(self, external_id: str) -> MFAState:
        """Return real 2FA enrollment for a user.

        Reads TOTP + WebAuthn tables. Merges enrolled methods in a
        stable, human-meaningful order: webauthn first (hardware),
        totp second. The ``last_used_method`` reflects whichever
        enrollment has the most recent ``last_used_at`` timestamp.
        """
        if not external_id or not self._path.is_file():
            return MFAState.none()

        methods: list[str] = []
        latest_ts = ""
        latest_method = ""

        webauthn_rows = self._read_webauthn(external_id)
        if webauthn_rows:
            methods.append("webauthn")
            for row in webauthn_rows:
                ts = str(row.get("last_used_at") or "")
                if ts and ts > latest_ts:
                    latest_ts = ts
                    latest_method = "webauthn"

        totp_info = self._read_totp(external_id)
        if totp_info is not None:
            methods.append("totp")
            if totp_info.last_used_at and totp_info.last_used_at > latest_ts:
                latest_ts = totp_info.last_used_at
                latest_method = "totp"

        if not methods:
            return MFAState.none()
        return MFAState(
            enrolled=True,
            enrolled_methods=tuple(methods),
            last_used_method=latest_method,
            last_used_at=latest_ts,
        )

    # ---- Authentication history ----------------------------------------

    def last_activity(self, external_id: str) -> str:
        """ISO-Z timestamp of the user's most recent SUCCESSFUL login.

        Failed attempts are excluded. Returns ``""`` when the user has
        never authenticated, when the log table doesn't exist yet
        (fresh install), or when the DB is unreachable.
        """
        if not external_id or not self._path.is_file():
            return ""
        rows = self._query(
            f"SELECT time FROM {_AUTH_LOG_TABLE} "
            "WHERE username = ? AND successful = 1 "
            "ORDER BY time DESC LIMIT 1",
            (external_id,),
            ignore_missing_table=True,
        )
        if not rows:
            return ""
        ts = rows[0].get("time")
        if ts is None:
            return ""
        try:
            dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        except (ValueError, TypeError, OverflowError):
            return ""
        return dt.isoformat().replace("+00:00", "Z")

    # ---- internals -----------------------------------------------------

    def _read_totp(self, username: str) -> _TOTPInfo | None:
        rows = self._query(
            f"SELECT created_at, last_used_at FROM {_TOTP_TABLE} "
            "WHERE username = ? LIMIT 1",
            (username,),
            ignore_missing_table=True,
        )
        if not rows:
            return None
        row = rows[0]
        return _TOTPInfo(
            created_at=str(row.get("created_at") or ""),
            last_used_at=str(row.get("last_used_at") or ""),
        )

    def _read_webauthn(self, username: str) -> list[dict[str, Any]]:
        for table in _WEBAUTHN_TABLE_CANDIDATES:
            rows = self._query(
                f"SELECT * FROM {table} WHERE username = ?",
                (username,),
                ignore_missing_table=True,
            )
            if rows is not None:
                return rows
        return []

    def _query(
        self,
        sql: str,
        params: tuple = (),
        *,
        ignore_missing_table: bool = False,
    ) -> list[dict[str, Any]] | None:
        """Run a read-only SQL query against Authelia's DB.

        Returns a list of dict rows on success, ``[]`` on any
        non-schema error (malformed SQL, IO), or ``None`` specifically
        when the target table does not exist AND
        ``ignore_missing_table`` is set (callers use this to probe
        table-name variants).
        """
        try:
            uri = f"file:{self._path}?mode=ro"
            con = sqlite3.connect(
                uri, uri=True, timeout=_SQLITE_TIMEOUT_SECONDS,
            )
        except sqlite3.Error as exc:
            _log.debug("[DEBUG] authelia sqlite open failed: %s", exc)
            return None if ignore_missing_table else []
        try:
            con.row_factory = sqlite3.Row
            cur = con.execute(sql, params)
            rows = cur.fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if ignore_missing_table and "no such table" in msg:
                return None
            _log.debug("[DEBUG] authelia sqlite query failed: %s", exc)
            return []
        except sqlite3.Error as exc:
            _log.debug("[DEBUG] authelia sqlite query failed: %s", exc)
            return []
        finally:
            con.close()


__all__ = ["AutheliaSessionAdmin"]
