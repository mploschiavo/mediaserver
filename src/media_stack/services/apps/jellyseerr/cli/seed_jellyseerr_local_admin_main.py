#!/usr/bin/env python3
"""Seed Jellyseerr local admin user from Kubernetes secret."""

from __future__ import annotations

import argparse
import base64
import os
import shlex
import time

from media_stack.cli.workflows.cli_common import kube_cmd, run_command
from media_stack.core.exceptions import ConfigError, MediaStackError

NODE_SCRIPT = r"""
const sqlite3 = require('sqlite3').verbose();
const bcrypt = require('bcrypt');

const decode = (v) => Buffer.from(v || '', 'base64').toString('utf8');
const email = decode(process.env.ADMIN_USER_B64);
const password = decode(process.env.ADMIN_PASS_B64);
const username = email;

if (!email || !password) {
  console.error('missing admin credentials');
  process.exit(2);
}

const db = new sqlite3.Database('/app/config/db/db.sqlite3');
const run = (sql, params = []) =>
  new Promise((resolve, reject) =>
    db.run(sql, params, function (err) {
      if (err) reject(err);
      else resolve(this);
    })
  );
const get = (sql, params = []) =>
  new Promise((resolve, reject) =>
    db.get(sql, params, (err, row) => {
      if (err) reject(err);
      else resolve(row);
    })
  );

(async () => {
  try {
    await run('BEGIN IMMEDIATE TRANSACTION');
    const hash = await bcrypt.hash(password, 12);
    const existing = await get(
      'SELECT id, email, username FROM user WHERE lower(email)=lower(?) OR lower(username)=lower(?) ORDER BY id LIMIT 1',
      [email, username]
    );

    let userId;
    if (existing) {
      userId = existing.id;
      await run(
        "UPDATE user SET email=?, username=?, password=?, permissions=(permissions | 2), userType=2, avatar=COALESCE(avatar,''), updatedAt=datetime('now') WHERE id=?",
        [email, username, hash, userId]
      );
      console.log('updated user id=' + userId + ' email=' + email);
    } else {
      const ins = await run(
        "INSERT INTO user (email, username, permissions, avatar, password, userType, createdAt, updatedAt) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
        [email, username, 2, '', hash, 2]
      );
      userId = ins.lastID;
      console.log('created user id=' + userId + ' email=' + email);
    }

    const userSettings = await get('SELECT id FROM user_settings WHERE userId=?', [userId]);
    if (!userSettings) {
      await run("INSERT INTO user_settings (locale, userId) VALUES ('en', ?)", [userId]);
      console.log('created user_settings for user id=' + userId);
    }

    await run('COMMIT');
    console.log('seed complete');
  } catch (err) {
    try {
      await run('ROLLBACK');
    } catch (_) {}
    console.error('seed failed:', err && err.message ? err.message : err);
    process.exit(1);
  } finally {
    db.close();
  }
})();
"""


class SeedJellyseerrLocalAdminMain:

    def parse_args(self, argv: list[str] | None = None) -> argparse.Namespace:
        parser = argparse.ArgumentParser(
            prog="bin/seed-jellyseerr-local-admin.sh",
            description="Seed Jellyseerr local admin from STACK_ADMIN credentials in Kubernetes secret.",
        )
        parser.add_argument(
            "--namespace",
            default=(os.environ.get("NAMESPACE", "media-stack") or "media-stack"),
            help="Kubernetes namespace (default: media-stack)",
        )
        parser.add_argument(
            "--secret-name",
            default=(os.environ.get("SECRET_NAME", "media-stack-secrets") or "media-stack-secrets"),
            help="Secret containing STACK_ADMIN_USERNAME/PASSWORD",
        )
        return parser.parse_args(argv)

    @staticmethod
    def _secret_field(kubectl: list[str], namespace: str, secret_name: str, field: str) -> str:
        proc = run_command(
            [
                *kubectl,
                "-n",
                namespace,
                "get",
                "secret",
                secret_name,
                "-o",
                f"jsonpath={{.data.{field}}}",
            ],
            check=True,
        )
        encoded = (proc.stdout or "").strip()
        if not encoded:
            return ""
        return base64.b64decode(encoded).decode("utf-8", errors="ignore").strip()

    @staticmethod
    def _first_pod(kubectl: list[str], namespace: str, selector: str) -> str:
        proc = run_command(
            [
                *kubectl,
                "-n",
                namespace,
                "get",
                "pod",
                "-l",
                selector,
                "-o",
                "jsonpath={.items[0].metadata.name}",
            ],
            check=True,
        )
        return (proc.stdout or "").strip()

    @staticmethod
    def _wait_for_rollout(kubectl: list[str], namespace: str) -> None:
        run_command(
            [
                *kubectl,
                "-n",
                namespace,
                "rollout",
                "status",
                "deployment/jellyseerr",
                "--timeout=180s",
            ],
            check=True,
        )

    def main(self, argv: list[str] | None = None) -> int:
        args = parse_args(argv)
        kubectl = kube_cmd()

        admin_user = _secret_field(kubectl, args.namespace, args.secret_name, "STACK_ADMIN_USERNAME")
        admin_pass = _secret_field(kubectl, args.namespace, args.secret_name, "STACK_ADMIN_PASSWORD")
        if not admin_user or not admin_pass:
            raise ConfigError(
                f"STACK_ADMIN_USERNAME / STACK_ADMIN_PASSWORD missing in secret "
                f"{args.namespace}/{args.secret_name}."
            )

        _wait_for_rollout(kubectl, args.namespace)
        pod = _first_pod(kubectl, args.namespace, "app=jellyseerr")
        if not pod:
            raise MediaStackError(f"Could not find jellyseerr pod in namespace {args.namespace}.")

        user_b64 = base64.b64encode(admin_user.encode("utf-8")).decode("ascii")
        pass_b64 = base64.b64encode(admin_pass.encode("utf-8")).decode("ascii")

        print(f"[INFO] Seeding Jellyseerr local admin in pod {pod} (email/username: {admin_user})")
        shell_script = (
            "cd /app && "
            f"ADMIN_USER_B64={shlex.quote(user_b64)} "
            f"ADMIN_PASS_B64={shlex.quote(pass_b64)} "
            "node - <<'NODE'\n"
            f"{NODE_SCRIPT}\n"
            "NODE"
        )
        seed_cmd = [*kubectl, "-n", args.namespace, "exec", pod, "--", "sh", "-lc", shell_script]
        proc = run_command(seed_cmd, check=False)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            if (
                "container not found" in detail.lower()
                or "unable to upgrade connection" in detail.lower()
            ):
                print(
                    "[WARN] Jellyseerr pod changed during seed attempt; retrying once after rollout check."
                )
                time.sleep(3)
                _wait_for_rollout(kubectl, args.namespace)
                pod = _first_pod(kubectl, args.namespace, "app=jellyseerr")
                if not pod:
                    raise MediaStackError(
                        f"Could not find jellyseerr pod in namespace {args.namespace} after retry."
                    )
                seed_cmd = [
                    *kubectl,
                    "-n",
                    args.namespace,
                    "exec",
                    pod,
                    "--",
                    "sh",
                    "-lc",
                    shell_script,
                ]
                proc = run_command(seed_cmd, check=False)
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "").strip() or f"exit code {proc.returncode}"
                raise MediaStackError(f"Jellyseerr seed failed: {detail}")

        print("[INFO] Restarting jellyseerr deployment to ensure settings are reloaded")
        run_command(
            [*kubectl, "-n", args.namespace, "rollout", "restart", "deployment/jellyseerr"],
            check=True,
        )
        run_command(
            [
                *kubectl,
                "-n",
                args.namespace,
                "rollout",
                "status",
                "deployment/jellyseerr",
                "--timeout=180s",
            ],
            check=True,
        )
        print("[OK] Jellyseerr local admin is seeded and deployment is ready.")
        return 0


_instance = SeedJellyseerrLocalAdminMain()
parse_args = _instance.parse_args
main = _instance.main


if __name__ == "__main__":
    raise SystemExit(main())
_first_pod = _instance._first_pod
_secret_field = _instance._secret_field
_wait_for_rollout = _instance._wait_for_rollout
