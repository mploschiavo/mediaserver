#!/usr/bin/env python3
"""Generate/update media-stack secrets and write local env export file.

Behavior mirrors scripts/generate-secrets.sh while keeping shell wrappers thin.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import string
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from core.exceptions import ConfigError, MediaStackError
from core.kube import resolve_kubectl_binary

SECRET_KEY_DEFAULTS: dict[str, str] = {
    "STACK_ADMIN_USERNAME": "",
    "STACK_ADMIN_PASSWORD": "",
    "SABNZBD_API_KEY": "",
    "JELLYFIN_API_KEY": "",
    "JELLYFIN_USER_ID": "",
    "JELLYSEERR_API_KEY": "",
    "TAUTULLI_API_KEY": "",
    "SONARR_API_KEY": "",
    "RADARR_API_KEY": "",
    "LIDARR_API_KEY": "",
    "READARR_API_KEY": "",
    "PROWLARR_API_KEY": "",
}


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _safe_b64decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return base64.b64decode(value).decode("utf-8")
    except Exception:
        return ""


def _run(cmd: list[str], *, check: bool = True, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=check,
        capture_output=True,
        text=True,
        input=input_text,
    )


def _rand_secret(length: int) -> str:
    alphabet = string.ascii_letters + string.digits + "@#%+=:._-"
    return "".join(secrets.choice(alphabet) for _ in range(max(1, length)))


@dataclass(frozen=True)
class GenerateSecretsConfig:
    namespace: str
    secret_name: str
    output_file: Path
    rotate_existing: bool
    pass_length: int
    stack_admin_user: str


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts/generate-secrets.sh",
        description="Generate/update Kubernetes media-stack secret and local env output.",
    )
    parser.add_argument("--namespace", default=os.environ.get("NAMESPACE", "media-stack"))
    parser.add_argument("--secret-name", default=os.environ.get("SECRET_NAME", "media-stack-secrets"))
    parser.add_argument("--output-file", default=os.environ.get("OUTPUT_FILE", "./secrets.generated.env"))
    parser.add_argument(
        "--rotate-existing",
        action="store_true",
        default=_env_truthy("ROTATE_EXISTING", False),
    )
    parser.add_argument(
        "--pass-length",
        type=int,
        default=int(os.environ.get("PASS_LENGTH", "24")),
    )
    parser.add_argument(
        "--stack-admin-user",
        default=os.environ.get("STACK_ADMIN_USER", ""),
    )
    return parser


def parse_config(argv: list[str] | None = None) -> GenerateSecretsConfig:
    args = _build_parser().parse_args(argv)
    namespace = str(args.namespace or "").strip() or "media-stack"
    secret_name = str(args.secret_name or "").strip() or "media-stack-secrets"
    output_file = Path(str(args.output_file or "./secrets.generated.env")).expanduser()
    pass_length = int(args.pass_length)
    if pass_length <= 0:
        raise ConfigError("PASS_LENGTH must be greater than zero.")
    stack_admin_user = str(args.stack_admin_user or "").strip()
    return GenerateSecretsConfig(
        namespace=namespace,
        secret_name=secret_name,
        output_file=output_file,
        rotate_existing=bool(args.rotate_existing),
        pass_length=pass_length,
        stack_admin_user=stack_admin_user,
    )


def _get_secret_payload(kubectl: list[str], namespace: str, secret_name: str) -> dict[str, str]:
    proc = _run([*kubectl, "-n", namespace, "get", "secret", secret_name, "-o", "json"], check=False)
    if proc.returncode != 0:
        return {}
    try:
        payload = json.loads(proc.stdout or "{}")
    except Exception:
        return {}
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return {}
    return {str(k): _safe_b64decode(str(v)) for k, v in data.items()}


def _apply_secret(
    kubectl: list[str],
    namespace: str,
    secret_name: str,
    values: dict[str, str],
) -> None:
    ordered_keys = [*SECRET_KEY_DEFAULTS.keys(), *sorted(k for k in values.keys() if k not in SECRET_KEY_DEFAULTS)]
    lines = [
        "apiVersion: v1",
        "kind: Secret",
        "metadata:",
        f"  name: {secret_name}",
        f"  namespace: {namespace}",
        "type: Opaque",
        "stringData:",
    ]
    for key in ordered_keys:
        lines.append(f"  {key}: {json.dumps(str(values.get(key, '')))}")
    manifest = "\n".join(lines) + "\n"
    proc = _run([*kubectl, "apply", "-f", "-"], input_text=manifest)
    if proc.stdout.strip():
        print(proc.stdout.strip())


def build_secret_values(
    *,
    current: dict[str, str],
    stack_admin_user: str,
    pass_length: int,
    rotate_existing: bool,
) -> dict[str, str]:
    values = {**SECRET_KEY_DEFAULTS, **{str(k): str(v) for k, v in current.items()}}

    stack_user = str(values.get("STACK_ADMIN_USERNAME", "")).strip()
    stack_pass = str(values.get("STACK_ADMIN_PASSWORD", "")).strip()
    if rotate_existing:
        if not stack_admin_user:
            raise ConfigError(
                "STACK_ADMIN_USER is required when rotating credentials "
                "(--stack-admin-user or env STACK_ADMIN_USER)."
            )
        stack_user = stack_admin_user
    elif not stack_user:
        if not stack_admin_user:
            raise ConfigError(
                "STACK_ADMIN_USERNAME is missing from secret and no STACK_ADMIN_USER was provided."
            )
        stack_user = stack_admin_user

    if not stack_pass or rotate_existing:
        stack_pass = _rand_secret(pass_length)

    values["STACK_ADMIN_USERNAME"] = stack_user
    values["STACK_ADMIN_PASSWORD"] = stack_pass

    return values


def _write_output(path: Path, values: dict[str, str], namespace: str, secret_name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "# Generated by scripts/generate-secrets.sh\n"
        "# Keep this file private.\n"
        f"NAMESPACE={namespace}\n"
        f"SECRET_NAME={secret_name}\n"
        f"STACK_ADMIN_USERNAME={values['STACK_ADMIN_USERNAME']}\n"
        f"STACK_ADMIN_PASSWORD={values['STACK_ADMIN_PASSWORD']}\n"
        f"SABNZBD_API_KEY={values['SABNZBD_API_KEY']}\n"
        f"JELLYFIN_API_KEY={values['JELLYFIN_API_KEY']}\n"
        f"JELLYFIN_USER_ID={values['JELLYFIN_USER_ID']}\n"
        f"JELLYSEERR_API_KEY={values['JELLYSEERR_API_KEY']}\n"
        f"TAUTULLI_API_KEY={values['TAUTULLI_API_KEY']}\n"
        f"SONARR_API_KEY={values['SONARR_API_KEY']}\n"
        f"RADARR_API_KEY={values['RADARR_API_KEY']}\n"
        f"LIDARR_API_KEY={values['LIDARR_API_KEY']}\n"
        f"READARR_API_KEY={values['READARR_API_KEY']}\n"
        f"PROWLARR_API_KEY={values['PROWLARR_API_KEY']}\n"
    )
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o600)


def run(cfg: GenerateSecretsConfig) -> int:
    kubectl = resolve_kubectl_binary()
    current = _get_secret_payload(kubectl, cfg.namespace, cfg.secret_name)
    existed = bool(current)

    values = build_secret_values(
        current=current,
        stack_admin_user=cfg.stack_admin_user,
        pass_length=cfg.pass_length,
        rotate_existing=cfg.rotate_existing,
    )

    _apply_secret(kubectl, cfg.namespace, cfg.secret_name, values)
    _write_output(cfg.output_file, values, cfg.namespace, cfg.secret_name)

    if existed:
        print(f"[OK] Updated secret {cfg.namespace}/{cfg.secret_name}")
    else:
        print(f"[OK] Created secret {cfg.namespace}/{cfg.secret_name}")
    print(f"[OK] Wrote generated credentials to {cfg.output_file} (mode 600)")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        cfg = parse_config(argv)
        return run(cfg)
    except (ConfigError, MediaStackError, subprocess.SubprocessError, OSError, ValueError) as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
