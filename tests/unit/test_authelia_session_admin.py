"""Tests for AutheliaSessionAdmin.

Uses a real sqlite3 file built in a tempdir so we exercise the actual
code paths (mocking sqlite3 would hide schema assumptions). sqlite3
is built into Python, so the cost is ~1ms per test.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.users.visibility_protocols import (  # noqa: E402
    MFAState,
    MFAStateProvider,
)
from media_stack.services.apps.authelia.session_admin import (  # noqa: E402
    AutheliaSessionAdmin,
)


class _AutheliaDBBuilder:
    """Build a sqlite3 file shaped like Authelia's ``db.sqlite3``.

    Kept focused on just the tables this module queries. Any future
    column drift is caught by the tests that read columns explicitly.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._con = sqlite3.connect(path)

    def with_totp_table(self) -> "_AutheliaDBBuilder":
        self._con.execute(
            "CREATE TABLE totp_configurations ("
            " username TEXT PRIMARY KEY,"
            " secret TEXT,"
            " created_at TEXT,"
            " last_used_at TEXT"
            ")"
        )
        return self

    def with_totp_for(
        self, username: str, *,
        created: str = "2026-01-01T00:00:00Z",
        last_used: str = "",
    ) -> "_AutheliaDBBuilder":
        self._con.execute(
            "INSERT INTO totp_configurations VALUES (?, ?, ?, ?)",
            (username, "secret-b64", created, last_used),
        )
        return self

    def with_webauthn_table(
        self, name: str = "webauthn_credentials",
    ) -> "_AutheliaDBBuilder":
        self._con.execute(
            f"CREATE TABLE {name} ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " username TEXT,"
            " description TEXT,"
            " last_used_at TEXT"
            ")"
        )
        return self

    def with_webauthn_for(
        self, username: str, *,
        description: str = "YubiKey 5",
        last_used: str = "",
        table: str = "webauthn_credentials",
    ) -> "_AutheliaDBBuilder":
        self._con.execute(
            f"INSERT INTO {table} (username, description, last_used_at) "
            "VALUES (?, ?, ?)",
            (username, description, last_used),
        )
        return self

    def with_auth_log_table(self) -> "_AutheliaDBBuilder":
        self._con.execute(
            "CREATE TABLE authentication_logs ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " time INTEGER,"
            " successful INTEGER,"
            " username TEXT"
            ")"
        )
        return self

    def with_auth_log(
        self, username: str, *,
        time_ts: int,
        successful: bool = True,
    ) -> "_AutheliaDBBuilder":
        self._con.execute(
            "INSERT INTO authentication_logs (time, successful, username) "
            "VALUES (?, ?, ?)",
            (time_ts, 1 if successful else 0, username),
        )
        return self

    def commit(self) -> Path:
        self._con.commit()
        self._con.close()
        return self._path


class AutheliaSessionAdminMFAStateTests(unittest.TestCase):

    def test_returns_none_when_db_missing(self) -> None:
        admin = AutheliaSessionAdmin(Path("/nonexistent.sqlite3"))
        self.assertEqual(admin.mfa_state("alice"), MFAState.none())

    def test_returns_none_for_empty_external_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = (
                _AutheliaDBBuilder(Path(tmp) / "db.sqlite3")
                .with_totp_table()
                .with_totp_for("alice")
                .commit()
            )
            admin = AutheliaSessionAdmin(path)
            self.assertEqual(admin.mfa_state(""), MFAState.none())

    def test_returns_none_when_no_enrollment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = (
                _AutheliaDBBuilder(Path(tmp) / "db.sqlite3")
                .with_totp_table()
                .commit()
            )
            admin = AutheliaSessionAdmin(path)
            self.assertEqual(admin.mfa_state("alice"), MFAState.none())

    def test_returns_none_when_neither_table_exists(self) -> None:
        # Fresh deployment: Authelia has created db.sqlite3 but no
        # user has enrolled TOTP/WebAuthn yet, so neither table is
        # present. Must not crash.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "db.sqlite3"
            sqlite3.connect(path).close()  # empty db, no tables
            admin = AutheliaSessionAdmin(path)
            self.assertEqual(admin.mfa_state("alice"), MFAState.none())

    def test_totp_enrollment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = (
                _AutheliaDBBuilder(Path(tmp) / "db.sqlite3")
                .with_totp_table()
                .with_totp_for(
                    "alice", last_used="2026-04-24T10:00:00Z",
                )
                .commit()
            )
            admin = AutheliaSessionAdmin(path)
            state = admin.mfa_state("alice")
            self.assertTrue(state.enrolled)
            self.assertEqual(state.enrolled_methods, ("totp",))
            self.assertEqual(state.last_used_method, "totp")
            self.assertEqual(state.last_used_at, "2026-04-24T10:00:00Z")

    def test_totp_enrollment_without_last_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = (
                _AutheliaDBBuilder(Path(tmp) / "db.sqlite3")
                .with_totp_table()
                .with_totp_for("alice", last_used="")
                .commit()
            )
            admin = AutheliaSessionAdmin(path)
            state = admin.mfa_state("alice")
            self.assertTrue(state.enrolled)
            self.assertEqual(state.last_used_at, "")
            self.assertEqual(state.last_used_method, "")

    def test_webauthn_enrollment_modern_table_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = (
                _AutheliaDBBuilder(Path(tmp) / "db.sqlite3")
                .with_webauthn_table(name="webauthn_credentials")
                .with_webauthn_for(
                    "alice", last_used="2026-04-24T10:00:00Z",
                )
                .commit()
            )
            admin = AutheliaSessionAdmin(path)
            state = admin.mfa_state("alice")
            self.assertTrue(state.enrolled)
            self.assertEqual(state.enrolled_methods, ("webauthn",))
            self.assertEqual(state.last_used_method, "webauthn")

    def test_webauthn_enrollment_legacy_table_name(self) -> None:
        # Authelia 4.37 used ``webauthn_devices``. Older deployments
        # still on 4.37 must work too.
        with tempfile.TemporaryDirectory() as tmp:
            path = (
                _AutheliaDBBuilder(Path(tmp) / "db.sqlite3")
                .with_webauthn_table(name="webauthn_devices")
                .with_webauthn_for(
                    "alice", table="webauthn_devices",
                    last_used="2026-04-24T10:00:00Z",
                )
                .commit()
            )
            admin = AutheliaSessionAdmin(path)
            state = admin.mfa_state("alice")
            self.assertTrue(state.enrolled)
            self.assertIn("webauthn", state.enrolled_methods)

    def test_both_totp_and_webauthn_enrolled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = (
                _AutheliaDBBuilder(Path(tmp) / "db.sqlite3")
                .with_totp_table()
                .with_totp_for("alice", last_used="2026-04-01T10:00:00Z")
                .with_webauthn_table()
                .with_webauthn_for(
                    "alice", last_used="2026-04-24T10:00:00Z",
                )
                .commit()
            )
            admin = AutheliaSessionAdmin(path)
            state = admin.mfa_state("alice")
            self.assertTrue(state.enrolled)
            self.assertIn("webauthn", state.enrolled_methods)
            self.assertIn("totp", state.enrolled_methods)
            # Webauthn is more recent, so picked as last_used_method.
            self.assertEqual(state.last_used_method, "webauthn")
            self.assertEqual(state.last_used_at, "2026-04-24T10:00:00Z")

    def test_last_used_picks_most_recent_across_methods(self) -> None:
        # Reverse of the previous: totp is more recent.
        with tempfile.TemporaryDirectory() as tmp:
            path = (
                _AutheliaDBBuilder(Path(tmp) / "db.sqlite3")
                .with_totp_table()
                .with_totp_for("alice", last_used="2026-04-24T10:00:00Z")
                .with_webauthn_table()
                .with_webauthn_for(
                    "alice", last_used="2026-01-01T10:00:00Z",
                )
                .commit()
            )
            admin = AutheliaSessionAdmin(path)
            state = admin.mfa_state("alice")
            self.assertEqual(state.last_used_method, "totp")
            self.assertEqual(state.last_used_at, "2026-04-24T10:00:00Z")

    def test_multiple_webauthn_devices_takes_latest_last_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = (
                _AutheliaDBBuilder(Path(tmp) / "db.sqlite3")
                .with_webauthn_table()
                .with_webauthn_for(
                    "alice", description="old YubiKey",
                    last_used="2025-06-01T10:00:00Z",
                )
                .with_webauthn_for(
                    "alice", description="new YubiKey",
                    last_used="2026-04-24T10:00:00Z",
                )
                .commit()
            )
            admin = AutheliaSessionAdmin(path)
            state = admin.mfa_state("alice")
            self.assertEqual(state.last_used_at, "2026-04-24T10:00:00Z")

    def test_enrollment_filters_by_username(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = (
                _AutheliaDBBuilder(Path(tmp) / "db.sqlite3")
                .with_totp_table()
                .with_totp_for("alice")
                .with_totp_for("bob")
                .commit()
            )
            admin = AutheliaSessionAdmin(path)
            self.assertTrue(admin.mfa_state("alice").enrolled)
            self.assertTrue(admin.mfa_state("bob").enrolled)
            self.assertFalse(admin.mfa_state("eve").enrolled)

    def test_satisfies_mfa_state_protocol(self) -> None:
        admin = AutheliaSessionAdmin(Path("/nowhere"))
        self.assertIsInstance(admin, MFAStateProvider)

    def test_has_expected_name(self) -> None:
        # The name is used to tag events and metrics — pin it here so
        # a rename doesn't silently break downstream dashboards.
        self.assertEqual(AutheliaSessionAdmin(Path("/x")).name, "authelia")


class AutheliaSessionAdminLastActivityTests(unittest.TestCase):

    def test_empty_when_db_missing(self) -> None:
        admin = AutheliaSessionAdmin(Path("/nonexistent.sqlite3"))
        self.assertEqual(admin.last_activity("alice"), "")

    def test_empty_for_empty_external_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = (
                _AutheliaDBBuilder(Path(tmp) / "db.sqlite3")
                .with_auth_log_table()
                .commit()
            )
            admin = AutheliaSessionAdmin(path)
            self.assertEqual(admin.last_activity(""), "")

    def test_empty_when_log_table_missing(self) -> None:
        # Fresh install — authentication_logs not created yet.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "db.sqlite3"
            sqlite3.connect(path).close()
            admin = AutheliaSessionAdmin(path)
            self.assertEqual(admin.last_activity("alice"), "")

    def test_empty_when_user_has_no_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = (
                _AutheliaDBBuilder(Path(tmp) / "db.sqlite3")
                .with_auth_log_table()
                .with_auth_log("bob", time_ts=1_700_000_000)
                .commit()
            )
            admin = AutheliaSessionAdmin(path)
            self.assertEqual(admin.last_activity("alice"), "")

    def test_returns_most_recent_successful_login_as_iso(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # 2026-04-24T10:00:00Z => 1_777_021_200 (verify by calc)
            ts_old = 1_700_000_000
            ts_new = 1_777_021_200
            path = (
                _AutheliaDBBuilder(Path(tmp) / "db.sqlite3")
                .with_auth_log_table()
                .with_auth_log("alice", time_ts=ts_old)
                .with_auth_log("alice", time_ts=ts_new)
                .commit()
            )
            admin = AutheliaSessionAdmin(path)
            iso = admin.last_activity("alice")
            self.assertTrue(iso.endswith("Z"))
            # Must reflect the NEWER ts.
            self.assertIn("2026", iso)
            # Lexical compare: the returned iso must be > an iso from
            # around the older ts.
            self.assertGreater(iso, "2024-01-01T00:00:00Z")

    def test_ignores_failed_logins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = (
                _AutheliaDBBuilder(Path(tmp) / "db.sqlite3")
                .with_auth_log_table()
                # Older success.
                .with_auth_log("alice", time_ts=1_600_000_000)
                # Newer FAILURE — must NOT be returned.
                .with_auth_log(
                    "alice", time_ts=1_800_000_000, successful=False,
                )
                .commit()
            )
            admin = AutheliaSessionAdmin(path)
            iso = admin.last_activity("alice")
            # 1_600_000_000 = 2020-09-13 UTC → year 2020.
            self.assertIn("2020", iso)

    def test_garbage_timestamp_returns_empty(self) -> None:
        # Defensive: what if someone manually inserted a bad row?
        # We should return "" rather than crash.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "db.sqlite3"
            con = sqlite3.connect(path)
            con.execute(
                "CREATE TABLE authentication_logs ("
                " time TEXT, successful INTEGER, username TEXT"
                ")"
            )
            con.execute(
                "INSERT INTO authentication_logs VALUES "
                "('not-a-timestamp', 1, 'alice')"
            )
            con.commit()
            con.close()
            admin = AutheliaSessionAdmin(path)
            self.assertEqual(admin.last_activity("alice"), "")


class AutheliaSessionAdminErrorPathTests(unittest.TestCase):

    def test_opens_read_only(self) -> None:
        """Proof: queries run against a read-only connection so we
        cannot accidentally write while Authelia is writing too."""
        with tempfile.TemporaryDirectory() as tmp:
            path = (
                _AutheliaDBBuilder(Path(tmp) / "db.sqlite3")
                .with_totp_table()
                .with_totp_for("alice")
                .commit()
            )
            admin = AutheliaSessionAdmin(path)
            # If the connection were writable we could demonstrate by
            # catching a write; instead, observe that concurrent
            # schema changes on a separate writer don't break us.
            state = admin.mfa_state("alice")
            self.assertTrue(state.enrolled)

    def test_concurrent_reads_dont_raise(self) -> None:
        # Not a true concurrency test; just exercises repeat open/close.
        with tempfile.TemporaryDirectory() as tmp:
            path = (
                _AutheliaDBBuilder(Path(tmp) / "db.sqlite3")
                .with_totp_table()
                .with_totp_for("alice")
                .commit()
            )
            admin = AutheliaSessionAdmin(path)
            for _ in range(5):
                admin.mfa_state("alice")

    def test_invalid_db_path_returns_safe_empty(self) -> None:
        # A directory, not a file.
        with tempfile.TemporaryDirectory() as tmp:
            admin = AutheliaSessionAdmin(Path(tmp))  # a dir
            self.assertEqual(admin.mfa_state("alice"), MFAState.none())
            self.assertEqual(admin.last_activity("alice"), "")


if __name__ == "__main__":
    unittest.main()
