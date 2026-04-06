import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import bcrypt

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.jellyseerr.local_admin_ops import ensure_local_admin_user  # noqa: E402


class _StubSvc:
    def __init__(self):
        self.logs: list[str] = []

    def log(self, msg: str) -> None:
        self.logs.append(str(msg))

    @staticmethod
    def bool_cfg(cfg, key, default=False):
        return bool((cfg or {}).get(key, default))


def _prepare_db(root: Path) -> Path:
    db_path = root / "jellyseerr" / "db" / "db.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE user ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "email TEXT,"
            "username TEXT,"
            "permissions INTEGER DEFAULT 0,"
            "avatar TEXT,"
            "password TEXT,"
            "userType INTEGER DEFAULT 0,"
            "createdAt TEXT,"
            "updatedAt TEXT)"
        )
        conn.execute(
            "CREATE TABLE user_settings ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "locale TEXT,"
            "userId INTEGER)"
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


class JellyseerrLocalAdminOpsTests(unittest.TestCase):
    def test_creates_local_admin_user_and_settings(self):
        svc = _StubSvc()
        cfg = {
            "jellyseerr": {"enabled": True},
            "app_auth": {
                "username_env": "STACK_ADMIN_USERNAME",
                "password_env": "STACK_ADMIN_PASSWORD",
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = _prepare_db(root)
            with mock.patch.dict(
                os.environ,
                {
                    "STACK_ADMIN_USERNAME": "admin",
                    "STACK_ADMIN_PASSWORD": "media-dev",
                },
                clear=False,
            ):
                ensure_local_admin_user(svc, cfg, str(root))

            conn = sqlite3.connect(str(db_path))
            try:
                row = conn.execute(
                    "SELECT id, email, username, permissions, userType, password FROM user"
                ).fetchone()
                self.assertIsNotNone(row)
                user_id = int(row[0])
                self.assertEqual(row[1], "admin")
                self.assertEqual(row[2], "admin")
                self.assertEqual(int(row[3]) & 2, 2)
                self.assertEqual(int(row[4]), 2)
                self.assertNotEqual(row[5], "media-dev")
                self.assertTrue(bcrypt.checkpw(b"media-dev", str(row[5]).encode("utf-8")))
                settings_row = conn.execute(
                    "SELECT locale, userId FROM user_settings WHERE userId=?",
                    (user_id,),
                ).fetchone()
                self.assertEqual(settings_row, ("en", user_id))
            finally:
                conn.close()

        self.assertTrue(
            any("local-admin seed created user" in message for message in svc.logs),
            msg=f"expected creation log, got logs={svc.logs}",
        )

    def test_updates_existing_user_and_retains_row_id(self):
        svc = _StubSvc()
        cfg = {
            "jellyseerr": {"enabled": True},
            "app_auth": {
                "username_env": "STACK_ADMIN_USERNAME",
                "password_env": "STACK_ADMIN_PASSWORD",
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = _prepare_db(root)
            conn = sqlite3.connect(str(db_path))
            try:
                cur = conn.execute(
                    "INSERT INTO user "
                    "(email, username, permissions, avatar, password, userType, createdAt, updatedAt) "
                    "VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
                    ("old-admin", "old-admin", 0, "", "old-hash", 1),
                )
                existing_id = int(cur.lastrowid or 0)
                conn.commit()
            finally:
                conn.close()

            with mock.patch.dict(
                os.environ,
                {
                    "STACK_ADMIN_USERNAME": "old-admin",
                    "STACK_ADMIN_PASSWORD": "new-pass",
                },
                clear=False,
            ):
                ensure_local_admin_user(svc, cfg, str(root))

            conn = sqlite3.connect(str(db_path))
            try:
                row = conn.execute(
                    "SELECT id, permissions, userType, password FROM user WHERE username=?",
                    ("old-admin",),
                ).fetchone()
            finally:
                conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(int(row[0]), existing_id)
        self.assertEqual(int(row[1]) & 2, 2)
        self.assertEqual(int(row[2]), 2)
        self.assertTrue(bcrypt.checkpw(b"new-pass", str(row[3]).encode("utf-8")))
        self.assertTrue(
            any("local-admin seed updated user" in message for message in svc.logs),
            msg=f"expected update log, got logs={svc.logs}",
        )

    def test_warns_when_env_missing_and_required_false(self):
        svc = _StubSvc()
        cfg = {
            "jellyseerr": {
                "local_admin_seed": {
                    "enabled": True,
                    "required": False,
                    "username_env": "MISSING_USER",
                    "password_env": "MISSING_PASS",
                }
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            _prepare_db(Path(tmp))
            ensure_local_admin_user(svc, cfg, tmp)

        self.assertTrue(
            any("missing credential env values" in message for message in svc.logs),
            msg=f"expected missing env warning, got logs={svc.logs}",
        )

    def test_raises_when_env_missing_and_required_true(self):
        svc = _StubSvc()
        cfg = {
            "jellyseerr": {
                "local_admin_seed": {
                    "enabled": True,
                    "required": True,
                    "username_env": "MISSING_USER",
                    "password_env": "MISSING_PASS",
                }
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            _prepare_db(Path(tmp))
            with self.assertRaises(RuntimeError):
                ensure_local_admin_user(svc, cfg, tmp)


if __name__ == "__main__":
    unittest.main()
