#!/usr/bin/env python3
"""Ensure qBittorrent credentials are secret-backed and persistent.

This is the Python replacement for scripts/ensure-qbit-credentials.sh.
It keeps CLI/env compatibility while moving non-trivial control flow out of bash.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Iterable

from core.exceptions import ConfigError, MediaStackError
from core.kube import resolve_kubectl_binary


_TEMP_PASSWORD_RE = re.compile(r"temporary password[^:]*:\s*(.+)$", re.IGNORECASE)


def _truthy(raw: str | None, *, default: bool = False) -> bool:
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _run(
    cmd: list[str],
    *,
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        input=input_text,
    )
    if check and proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        detail = stderr or stdout or f"exit code {proc.returncode}"
        raise MediaStackError(f"Command failed ({' '.join(cmd)}): {detail}")
    return proc


@dataclass(frozen=True)
class EnsureQbitCredentialsConfig:
    namespace: str
    secret_name: str
    default_stack_admin_user: str
    default_stack_admin_pass: str
    default_qbit_user: str
    default_qbit_pass: str
    rollout_timeout: str
    qbit_wait_seconds: int
    qbit_deployment: str
    force_reset_on_auth_failure: bool
    qbit_force_config_sync: bool
    qbit_strict_login_check: bool
    qbit_api_validation: bool
    qbit_use_stack_admin: bool
    qbit_write_legacy_secret_keys: bool


@dataclass(frozen=True)
class CredentialResolution:
    stack_admin_user: str
    stack_admin_pass: str
    qb_user: str
    qb_pass: str


class KubeClient:
    def __init__(self, prefix: list[str], namespace: str) -> None:
        self._prefix = prefix
        self._namespace = namespace

    def run(self, args: Iterable[str], *, check: bool = True, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        cmd = [*self._prefix, *list(args)]
        return _run(cmd, check=check, input_text=input_text)

    def run_ns(self, args: Iterable[str], *, check: bool = True, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        cmd = [*self._prefix, "-n", self._namespace, *list(args)]
        return _run(cmd, check=check, input_text=input_text)

    def get_secret_value(self, secret_name: str, key: str) -> str:
        proc = self.run_ns(
            [
                "get",
                "secret",
                secret_name,
                "-o",
                f"jsonpath={{.data.{key}}}",
            ],
            check=False,
        )
        if proc.returncode != 0:
            return ""
        raw = (proc.stdout or "").strip()
        if not raw:
            return ""
        try:
            return base64.b64decode(raw).decode("utf-8")
        except Exception:
            return ""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts/ensure-qbit-credentials.sh",
        description=(
            "Ensure qBittorrent credentials are secret-backed and persisted in qB config."
        ),
    )
    return parser


def parse_config(argv: list[str] | None = None) -> EnsureQbitCredentialsConfig:
    _build_parser().parse_args(argv)

    default_stack_admin_user = os.environ.get("DEFAULT_STACK_ADMIN_USER", "admin").strip() or "admin"
    default_stack_admin_pass = (
        os.environ.get("DEFAULT_STACK_ADMIN_PASS", "media-stack-admin").strip() or "media-stack-admin"
    )

    return EnsureQbitCredentialsConfig(
        namespace=os.environ.get("NAMESPACE", "media-stack").strip() or "media-stack",
        secret_name=os.environ.get("SECRET_NAME", "media-stack-secrets").strip() or "media-stack-secrets",
        default_stack_admin_user=default_stack_admin_user,
        default_stack_admin_pass=default_stack_admin_pass,
        default_qbit_user=(os.environ.get("DEFAULT_QBIT_USER", default_stack_admin_user).strip() or default_stack_admin_user),
        default_qbit_pass=(os.environ.get("DEFAULT_QBIT_PASS", default_stack_admin_pass).strip() or default_stack_admin_pass),
        rollout_timeout=os.environ.get("ROLL_OUT_TIMEOUT", "5m").strip() or "5m",
        qbit_wait_seconds=max(1, int(os.environ.get("QBIT_WAIT_SECONDS", "120"))),
        qbit_deployment=os.environ.get("QBIT_DEPLOYMENT", "qbittorrent").strip() or "qbittorrent",
        force_reset_on_auth_failure=_truthy(os.environ.get("FORCE_RESET_ON_AUTH_FAILURE", "1"), default=True),
        qbit_force_config_sync=_truthy(os.environ.get("QBIT_FORCE_CONFIG_SYNC", "1"), default=True),
        qbit_strict_login_check=_truthy(os.environ.get("QBIT_STRICT_LOGIN_CHECK", "0"), default=False),
        qbit_api_validation=_truthy(os.environ.get("QBIT_API_VALIDATION", "0"), default=False),
        qbit_use_stack_admin=_truthy(os.environ.get("QBIT_USE_STACK_ADMIN", "1"), default=True),
        qbit_write_legacy_secret_keys=_truthy(os.environ.get("QBIT_WRITE_LEGACY_SECRET_KEYS", "0"), default=False),
    )


def ensure_secret_exists(kube: KubeClient, cfg: EnsureQbitCredentialsConfig) -> None:
    proc = kube.run_ns(["get", "secret", cfg.secret_name], check=False)
    if proc.returncode == 0:
        return

    manifest = f"""apiVersion: v1
kind: Secret
metadata:
  name: {cfg.secret_name}
  namespace: {cfg.namespace}
type: Opaque
stringData:
  STACK_ADMIN_USERNAME: "{cfg.default_stack_admin_user}"
  STACK_ADMIN_PASSWORD: "{cfg.default_stack_admin_pass}"
  JELLYFIN_API_KEY: ""
  JELLYFIN_USER_ID: ""
  UNPACKERR_SONARR_API_KEY: "replace-after-first-boot"
  UNPACKERR_RADARR_API_KEY: "replace-after-first-boot"
  UNPACKERR_LIDARR_API_KEY: "replace-after-first-boot"
  UNPACKERR_READARR_API_KEY: "replace-after-first-boot"
"""
    kube.run(["apply", "-f", "-"], input_text=manifest)
    print(f"[OK] Created {cfg.namespace}/{cfg.secret_name} with default stack admin credentials.")


def resolve_target_credentials(
    cfg: EnsureQbitCredentialsConfig,
    *,
    stack_admin_user: str,
    stack_admin_pass: str,
    legacy_qb_user: str,
    legacy_qb_pass: str,
) -> CredentialResolution:
    resolved_stack_user = stack_admin_user or cfg.default_stack_admin_user
    resolved_stack_pass = stack_admin_pass if stack_admin_pass and stack_admin_pass != "change-me" else cfg.default_stack_admin_pass

    qb_user = resolved_stack_user
    qb_pass = resolved_stack_pass

    if not cfg.qbit_use_stack_admin:
        qb_user = legacy_qb_user or cfg.default_qbit_user
        qb_pass = (
            legacy_qb_pass
            if legacy_qb_pass and legacy_qb_pass != "change-me"
            else cfg.default_qbit_pass
        )

    return CredentialResolution(
        stack_admin_user=resolved_stack_user,
        stack_admin_pass=resolved_stack_pass,
        qb_user=qb_user,
        qb_pass=qb_pass,
    )


def build_secret_patch(cfg: EnsureQbitCredentialsConfig, creds: CredentialResolution) -> dict[str, dict[str, str]]:
    write_legacy = cfg.qbit_write_legacy_secret_keys or not cfg.qbit_use_stack_admin
    string_data: dict[str, str] = {
        "STACK_ADMIN_USERNAME": creds.stack_admin_user,
        "STACK_ADMIN_PASSWORD": creds.stack_admin_pass,
    }
    if write_legacy:
        string_data["QBITTORRENT_USERNAME"] = creds.qb_user
        string_data["QBITTORRENT_PASSWORD"] = creds.qb_pass
    return {"stringData": string_data}


def patch_secret(kube: KubeClient, cfg: EnsureQbitCredentialsConfig, patch: dict[str, object]) -> None:
    kube.run_ns(
        [
            "patch",
            "secret",
            cfg.secret_name,
            "--type",
            "merge",
            "-p",
            json.dumps(patch, separators=(",", ":")),
        ]
    )


def qbit_login_in_pod(kube: KubeClient, cfg: EnsureQbitCredentialsConfig, username: str, password: str) -> bool:
    proc = kube.run_ns(
        [
            "exec",
            f"deploy/{cfg.qbit_deployment}",
            "--",
            "env",
            f"QB_USER={username}",
            f"QB_PASS={password}",
            "sh",
            "-lc",
            """
tmp_body="/tmp/qb-login-body.$$"
code="$(curl -sS -o "$tmp_body" -w "%{http_code}" \\
  -H "Origin: http://127.0.0.1:8080" \\
  -H "Referer: http://127.0.0.1:8080/" \\
  -H "User-Agent: media-stack-bootstrap/1.0" \\
  --data-urlencode "username=$QB_USER" \\
  --data-urlencode "password=$QB_PASS" \\
  "http://127.0.0.1:8080/api/v2/auth/login" 2>/dev/null || true)"
body="$(cat "$tmp_body" 2>/dev/null || true)"
rm -f "$tmp_body" >/dev/null 2>&1 || true
case "$code" in
  2*) [ "$body" = "Ok." ] || [ "${body#Ok.}" != "$body" ] ;;
  *) false ;;
esac
""",
        ],
        check=False,
    )
    return proc.returncode == 0


def qbit_set_webui_credentials_in_pod(
    kube: KubeClient,
    cfg: EnsureQbitCredentialsConfig,
    *,
    auth_user: str,
    auth_pass: str,
    target_user: str,
    target_pass: str,
) -> bool:
    proc = kube.run_ns(
        [
            "exec",
            f"deploy/{cfg.qbit_deployment}",
            "--",
            "env",
            f"AUTH_USER={auth_user}",
            f"AUTH_PASS={auth_pass}",
            f"TARGET_USER={target_user}",
            f"TARGET_PASS={target_pass}",
            "sh",
            "-lc",
            """
set -e
cookie="/tmp/qb-cookie.$$"
login_body="/tmp/qb-login.$$"
prefs_body="/tmp/qb-prefs.$$"

login_code="$(curl -sS -c "$cookie" -b "$cookie" \\
  --data-urlencode "username=$AUTH_USER" \\
  --data-urlencode "password=$AUTH_PASS" \\
  -o "$login_body" -w "%{http_code}" \\
  "http://127.0.0.1:8080/api/v2/auth/login" 2>/dev/null || true)"
login_text="$(cat "$login_body" 2>/dev/null || true)"

if [ "${login_code#2}" = "$login_code" ]; then
  rm -f "$cookie" "$login_body" "$prefs_body" >/dev/null 2>&1 || true
  exit 12
fi
case "$login_text" in
  Ok.*) ;;
  *)
    rm -f "$cookie" "$login_body" "$prefs_body" >/dev/null 2>&1 || true
    exit 13
    ;;
esac

prefs_json="$(printf '{\"web_ui_username\":\"%s\",\"web_ui_password\":\"%s\"}' "$TARGET_USER" "$TARGET_PASS")"
prefs_code="$(curl -sS -c "$cookie" -b "$cookie" \\
  --data-urlencode "json=$prefs_json" \\
  -o "$prefs_body" -w "%{http_code}" \\
  "http://127.0.0.1:8080/api/v2/app/setPreferences" 2>/dev/null || true)"

if [ "${prefs_code#2}" = "$prefs_code" ]; then
  rm -f "$cookie" "$login_body" "$prefs_body" >/dev/null 2>&1 || true
  exit 14
fi

rm -f "$cookie" "$login_body" "$prefs_body" >/dev/null 2>&1 || true
""",
        ],
        check=False,
    )
    return proc.returncode == 0


def try_inpod_reconcile_with_auth(
    kube: KubeClient,
    cfg: EnsureQbitCredentialsConfig,
    *,
    source_label: str,
    auth_user: str,
    auth_pass: str,
    target_user: str,
    target_pass: str,
) -> bool:
    if not qbit_set_webui_credentials_in_pod(
        kube,
        cfg,
        auth_user=auth_user,
        auth_pass=auth_pass,
        target_user=target_user,
        target_pass=target_pass,
    ):
        return False
    if qbit_login_in_pod(kube, cfg, target_user, target_pass):
        print(f"[OK] qBittorrent WebUI credentials reconciled from {source_label} via in-pod API.")
        return True
    return False


def rollout_restart_and_wait(kube: KubeClient, cfg: EnsureQbitCredentialsConfig, *, reason: str) -> None:
    print(f"[INFO] Restarting deploy/{cfg.qbit_deployment} after {reason}")
    kube.run_ns(["rollout", "restart", f"deploy/{cfg.qbit_deployment}"])
    proc = kube.run_ns(
        ["rollout", "status", f"deploy/{cfg.qbit_deployment}", f"--timeout={cfg.rollout_timeout}"],
        check=False,
    )
    if proc.returncode != 0:
        print(
            f"[WARN] deploy/{cfg.qbit_deployment} did not fully roll out in {cfg.rollout_timeout} after {reason}.",
            file=sys.stderr,
        )


def force_reset_qbit_auth(kube: KubeClient, cfg: EnsureQbitCredentialsConfig) -> bool:
    print("[WARN] Forcing qBittorrent WebUI auth reset in qB config files under /config")
    proc = kube.run_ns(
        [
            "exec",
            f"deploy/{cfg.qbit_deployment}",
            "--",
            "sh",
            "-lc",
            """
set -e
for f in $(find /config -maxdepth 6 -name qBittorrent.conf 2>/dev/null); do
  cp "$f" "${f}.bak.$(date +%s)" || true
  sed -i -e '/^WebUI\\Username=/d' -e '/^WebUI\\Password/d' "$f" || true
done
""",
        ],
        check=False,
    )
    if proc.returncode != 0:
        print("[WARN] Could not reset qB auth lines; continuing.", file=sys.stderr)
        return False
    rollout_restart_and_wait(kube, cfg, reason="auth reset")
    return True


def generate_qbit_pbkdf2_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha512", password.encode("utf-8"), salt, 100000)
    salt_b64 = base64.b64encode(salt).decode("ascii")
    digest_b64 = base64.b64encode(digest).decode("ascii")
    return f"@ByteArray({salt_b64}:{digest_b64})"


def sync_qbit_auth_config(kube: KubeClient, cfg: EnsureQbitCredentialsConfig, *, username: str, pbkdf2_hash: str) -> None:
    proc = kube.run_ns(
        [
            "exec",
            f"deploy/{cfg.qbit_deployment}",
            "--",
            "env",
            f"QBIT_USER_ESC={username}",
            f"QBIT_HASH_ESC={pbkdf2_hash}",
            "sh",
            "-lc",
            """
set -e
found=0
for f in $(find /config -maxdepth 6 -name qBittorrent.conf 2>/dev/null); do
  found=1
  cp "$f" "${f}.bak.$(date +%s)" || true
  sed -i -e '/^WebUI\\Username=/d' -e '/^WebUI\\Password_PBKDF2=/d' -e '/^WebUI\\Password_ha1=/d' "$f"
  {
    echo "WebUI\\Username=${QBIT_USER_ESC}"
    echo "WebUI\\Password_PBKDF2=${QBIT_HASH_ESC}"
  } >> "$f"
  echo "[INFO] Updated qB auth lines in $f"
done
if [ "$found" -eq 0 ]; then
  echo "[ERR] No qBittorrent.conf file found under /config" >&2
  exit 1
fi
""",
        ],
        check=False,
    )
    if proc.stdout.strip():
        print(proc.stdout.strip())
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        raise MediaStackError(stderr or "Failed syncing qBittorrent auth config")
    rollout_restart_and_wait(kube, cfg, reason="config sync")


def extract_temp_password_from_logs(kube: KubeClient, cfg: EnsureQbitCredentialsConfig, pod_name: str | None) -> str:
    logs: list[str] = []
    if pod_name:
        proc = kube.run_ns(["logs", pod_name, "--tail=300"], check=False)
        if proc.stdout:
            logs.append(proc.stdout)
    else:
        for args in (
            ["logs", f"deploy/{cfg.qbit_deployment}", "--tail=300"],
            ["logs", f"deploy/{cfg.qbit_deployment}", "--previous", "--tail=300"],
        ):
            proc = kube.run_ns(args, check=False)
            if proc.stdout:
                logs.append(proc.stdout)

    last_match = ""
    for chunk in logs:
        for line in chunk.splitlines():
            match = _TEMP_PASSWORD_RE.search(line)
            if match:
                candidate = match.group(1).strip().strip("\r")
                if candidate:
                    last_match = candidate
    return last_match


def wait_for_temp_password(
    kube: KubeClient,
    cfg: EnsureQbitCredentialsConfig,
    *,
    pod_name: str | None,
    max_wait_seconds: int,
) -> str:
    waited = 0
    while waited < max_wait_seconds:
        found = extract_temp_password_from_logs(kube, cfg, pod_name)
        if found:
            return found
        time.sleep(3)
        waited += 3
    return ""


def run(cfg: EnsureQbitCredentialsConfig) -> int:
    kube = KubeClient(resolve_kubectl_binary(), cfg.namespace)

    ensure_secret_exists(kube, cfg)

    stack_admin_user = kube.get_secret_value(cfg.secret_name, "STACK_ADMIN_USERNAME")
    stack_admin_pass = kube.get_secret_value(cfg.secret_name, "STACK_ADMIN_PASSWORD")
    legacy_qb_user = kube.get_secret_value(cfg.secret_name, "QBITTORRENT_USERNAME")
    legacy_qb_pass = kube.get_secret_value(cfg.secret_name, "QBITTORRENT_PASSWORD")

    creds = resolve_target_credentials(
        cfg,
        stack_admin_user=stack_admin_user,
        stack_admin_pass=stack_admin_pass,
        legacy_qb_user=legacy_qb_user,
        legacy_qb_pass=legacy_qb_pass,
    )

    patch_secret(kube, cfg, build_secret_patch(cfg, creds))
    print(
        f"[OK] Secret {cfg.namespace}/{cfg.secret_name} now has qBittorrent credentials for user '{creds.qb_user}'."
    )
    print(
        f"[INFO] Target qB credentials from secret: username='{creds.qb_user}', password_length={len(creds.qb_pass)}"
    )

    if qbit_login_in_pod(kube, cfg, creds.qb_user, creds.qb_pass):
        print("[OK] qBittorrent credentials validated from inside the qB pod.")
        return 0

    print("[WARN] In-pod qB credential check failed; continuing with recovery flow.")

    if try_inpod_reconcile_with_auth(
        kube,
        cfg,
        source_label="admin/adminadmin fallback",
        auth_user="admin",
        auth_pass="adminadmin",
        target_user=creds.qb_user,
        target_pass=creds.qb_pass,
    ):
        return 0

    stack_auth_user = creds.stack_admin_user or creds.qb_user
    if creds.stack_admin_pass and try_inpod_reconcile_with_auth(
        kube,
        cfg,
        source_label="stack-admin fallback credentials",
        auth_user=stack_auth_user,
        auth_pass=creds.stack_admin_pass,
        target_user=creds.qb_user,
        target_pass=creds.qb_pass,
    ):
        return 0

    temp_pass = wait_for_temp_password(kube, cfg, pod_name=None, max_wait_seconds=30)
    if temp_pass and try_inpod_reconcile_with_auth(
        kube,
        cfg,
        source_label="temporary startup password",
        auth_user="admin",
        auth_pass=temp_pass,
        target_user=creds.qb_user,
        target_pass=creds.qb_pass,
    ):
        return 0

    if cfg.force_reset_on_auth_failure:
        print("[WARN] In-pod fallback auth did not work; forcing qB auth reset and retrying once.")
        if force_reset_qbit_auth(kube, cfg):
            temp_pass = wait_for_temp_password(kube, cfg, pod_name=None, max_wait_seconds=90)
            if temp_pass and try_inpod_reconcile_with_auth(
                kube,
                cfg,
                source_label="temporary password after reset",
                auth_user="admin",
                auth_pass=temp_pass,
                target_user=creds.qb_user,
                target_pass=creds.qb_pass,
            ):
                return 0

    config_sync_done = False
    if cfg.qbit_force_config_sync:
        print("[INFO] qB deterministic credential sync enabled: writing PBKDF2 hash to qB config.")
        sync_qbit_auth_config(
            kube,
            cfg,
            username=creds.qb_user,
            pbkdf2_hash=generate_qbit_pbkdf2_hash(creds.qb_pass),
        )
        config_sync_done = True
        print("[OK] qBittorrent auth synced in config to match Kubernetes secret.")

    if config_sync_done and not cfg.qbit_api_validation and not cfg.qbit_strict_login_check:
        if qbit_login_in_pod(kube, cfg, creds.qb_user, creds.qb_pass):
            print("[INFO] qB API validation disabled (QBIT_API_VALIDATION=0); relying on deterministic config sync.")
            print("[OK] qBittorrent credentials have been applied from secret via config-as-code.")
            return 0
        print("[WARN] Deterministic config sync completed but in-pod login still failed; continuing with recovery flow.")

    if qbit_login_in_pod(kube, cfg, creds.qb_user, creds.qb_pass):
        print("[OK] qBittorrent WebUI credentials reconciled to secret values.")
        return 0

    if config_sync_done and not cfg.qbit_strict_login_check:
        print("[WARN] qB API validation still failed, but config sync has been applied.", file=sys.stderr)
        print(
            "[WARN] Continuing non-strict mode; downstream bootstrap will verify qB connectivity from inside cluster.",
            file=sys.stderr,
        )
        return 0

    print(
        "[ERR] Could not authenticate to qBittorrent with secret creds, admin/adminadmin, temporary startup password, or forced auth reset.",
        file=sys.stderr,
    )
    print("[ERR] Manual recovery:", file=sys.stderr)
    print("      bash scripts/reset-qbit-webui-auth.sh", file=sys.stderr)
    print("      bash scripts/set-qbit-secret.sh <USERNAME> <PASSWORD>", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    try:
        cfg = parse_config(argv)
        return run(cfg)
    except (ConfigError, MediaStackError, subprocess.SubprocessError, OSError, ValueError) as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
