"""Local-admin seeding operations for Jellyseerr."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

import bcrypt


class JellyseerrLocalAdminOps:

    @staticmethod
    def _cfg_bool(svc, cfg: dict[str, Any], key: str, default: bool) -> bool:
        return bool(svc.bool_cfg(cfg, key, default))

    @staticmethod
    def _cfg_text(cfg: dict[str, Any], key: str, default: str) -> str:
        return str(cfg.get(key, default) or default).strip()

    def ensure_local_admin_user(self, 
        svc,
        cfg: dict[str, Any],
        config_root: str,
    ) -> None:
        jelly_cfg = cfg.get("jellyseerr") or {}
        seed_cfg = jelly_cfg.get("local_admin_seed")
        if not isinstance(seed_cfg, dict):
            seed_cfg = {}
        if not _cfg_bool(svc, seed_cfg, "enabled", True):
            return

        app_auth_cfg = cfg.get("app_auth") or {}
        app_auth_user_env = (
            str(app_auth_cfg.get("username_env") or "").strip()
            if isinstance(app_auth_cfg, dict)
            else ""
        )
        app_auth_pass_env = (
            str(app_auth_cfg.get("password_env") or "").strip()
            if isinstance(app_auth_cfg, dict)
            else ""
        )
        username_env = _cfg_text(
            seed_cfg,
            "username_env",
            app_auth_user_env or "STACK_ADMIN_USERNAME",
        )
        password_env = _cfg_text(
            seed_cfg,
            "password_env",
            app_auth_pass_env or "STACK_ADMIN_PASSWORD",
        )
        email_env = _cfg_text(seed_cfg, "email_env", username_env)

        username = str(os.environ.get(username_env) or "").strip()
        password = str(os.environ.get(password_env) or "").strip()
        email = str(os.environ.get(email_env) or "").strip()
        if not email:
            email = username

        required = _cfg_bool(svc, seed_cfg, "required", False)
        if not username or not password or not email:
            msg = (
                "Jellyseerr local-admin seed: missing credential env values "
                f"(username_env={username_env}, password_env={password_env}, email_env={email_env})."
            )
            if required:
                raise RuntimeError(msg)
            svc.log(f"[WARN] {msg}")
            return

        db_rel_path = _cfg_text(seed_cfg, "db_relative_path", "jellyseerr/db/db.sqlite3")
        db_path = Path(config_root) / db_rel_path
        if not db_path.exists():
            msg = f"Jellyseerr local-admin seed: db file not found at {db_path}"
            if required:
                raise RuntimeError(msg)
            svc.log(f"[WARN] {msg}; skipping local-admin seed.")
            return

        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode(
            "utf-8"
        )
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT id FROM user "
                "WHERE lower(email)=lower(?) OR lower(username)=lower(?) "
                "ORDER BY id LIMIT 1",
                (email, username),
            ).fetchone()

            user_id: int
            if row:
                user_id = int(row[0])
                conn.execute(
                    "UPDATE user "
                    "SET email=?, username=?, password=?, permissions=(permissions | 2), "
                    "userType=2, avatar=COALESCE(avatar,''), updatedAt=datetime('now') "
                    "WHERE id=?",
                    (email, username, password_hash, user_id),
                )
                action = "updated"
            else:
                cur = conn.execute(
                    "INSERT INTO user "
                    "(email, username, permissions, avatar, password, userType, createdAt, updatedAt) "
                    "VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
                    (email, username, 2, "", password_hash, 2),
                )
                user_id = int(cur.lastrowid or 0)
                if user_id <= 0:
                    raise RuntimeError(
                        "Jellyseerr local-admin seed: failed to resolve inserted user id."
                    )
                action = "created"

            settings_row = conn.execute(
                "SELECT id FROM user_settings WHERE userId=? LIMIT 1",
                (user_id,),
            ).fetchone()
            if not settings_row:
                conn.execute(
                    "INSERT INTO user_settings (locale, userId) VALUES (?, ?)",
                    ("en", user_id),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        svc.log(
            "[OK] Jellyseerr: local-admin seed "
            f"{action} user (email={email}, username={username}, user_id={user_id})"
        )


_instance = JellyseerrLocalAdminOps()
ensure_local_admin_user = _instance.ensure_local_admin_user
