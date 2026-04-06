#!/usr/bin/env python3
"""Hard-reset qB WebUI auth and reconcile from stack secret."""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from media_stack.cli.workflows.cli_common import kube_cmd, repo_root_from_script_file, run_command
from media_stack.core.exceptions import ConfigError, MediaStackError


@dataclass(frozen=True)
class ResetQbitWebuiAuthConfig:
    namespace: str
    deployment: str
    rollout_timeout: str
    root_dir: Path


def parse_config(argv: list[str] | None = None) -> ResetQbitWebuiAuthConfig:
    parser = argparse.ArgumentParser(
        prog="bin/reset-qbit-webui-auth.sh",
        description=(
            "Hard-reset qBittorrent WebUI auth in Kubernetes and reconcile it "
            "back to credentials in media-stack secret."
        ),
    )
    parser.parse_args(argv)
    return ResetQbitWebuiAuthConfig(
        namespace=os.environ.get("NAMESPACE", "media-stack").strip() or "media-stack",
        deployment=os.environ.get("DEPLOYMENT", "qbittorrent").strip() or "qbittorrent",
        rollout_timeout=os.environ.get("ROLL_OUT_TIMEOUT", "5m").strip() or "5m",
        root_dir=repo_root_from_script_file(__file__),
    )


def _locate_conf_path(kubectl: list[str], cfg: ResetQbitWebuiAuthConfig) -> str:
    proc = run_command(
        [
            *kubectl,
            "-n",
            cfg.namespace,
            "exec",
            f"deploy/{cfg.deployment}",
            "--",
            "sh",
            "-lc",
            """
for p in \
  /config/qBittorrent/qBittorrent.conf \
  /config/qBittorrent/config/qBittorrent.conf \
  /config/qBittorrent/data/qBittorrent/config/qBittorrent.conf
do
  if [ -f "$p" ]; then
    echo "$p"
    exit 0
  fi
done
find /config -maxdepth 6 -name qBittorrent.conf 2>/dev/null | head -n1
""",
        ],
        check=False,
    )
    return (proc.stdout or "").strip()


def _wait_until_no_pods(
    kubectl: list[str], cfg: ResetQbitWebuiAuthConfig, seconds: int = 60
) -> None:
    for _ in range(max(seconds, 1)):
        proc = run_command(
            [
                *kubectl,
                "-n",
                cfg.namespace,
                "get",
                "pods",
                "-l",
                "app=qbittorrent",
                "--no-headers",
            ],
            check=False,
        )
        rows = [line for line in (proc.stdout or "").splitlines() if line.strip()]
        if not rows:
            return
        time.sleep(1)


def run(cfg: ResetQbitWebuiAuthConfig) -> int:
    kubectl = kube_cmd()
    print(f"[INFO] Locating qBittorrent config file inside deploy/{cfg.deployment}")
    conf_path = _locate_conf_path(kubectl, cfg)
    if not conf_path:
        raise MediaStackError("Could not find qBittorrent.conf inside /config.")
    print(f"[INFO] Found config: {conf_path}")

    print("[INFO] Backing up and clearing WebUI auth lines")
    run_command(
        [
            *kubectl,
            "-n",
            cfg.namespace,
            "exec",
            f"deploy/{cfg.deployment}",
            "--",
            "sh",
            "-lc",
            f"""
set -e
cp '{conf_path}' '{conf_path}.bak.$(date +%s)'
sed -i -e '/^WebUI\\\\Username=/d' -e '/^WebUI\\\\Password_PBKDF2=/d' -e '/^WebUI\\\\Password_ha1=/d' '{conf_path}'
""",
        ]
    )

    print(f"[INFO] Scaling deploy/{cfg.deployment} to 0 for clean stop")
    run_command(
        [*kubectl, "-n", cfg.namespace, "scale", f"deploy/{cfg.deployment}", "--replicas=0"]
    )
    _wait_until_no_pods(kubectl, cfg)

    print(f"[INFO] Scaling deploy/{cfg.deployment} back to 1")
    run_command(
        [*kubectl, "-n", cfg.namespace, "scale", f"deploy/{cfg.deployment}", "--replicas=1"]
    )
    rollout_proc = run_command(
        [
            *kubectl,
            "-n",
            cfg.namespace,
            "rollout",
            "status",
            f"deploy/{cfg.deployment}",
            f"--timeout={cfg.rollout_timeout}",
        ],
        check=False,
    )
    sys.stdout.write(rollout_proc.stdout or "")
    sys.stderr.write(rollout_proc.stderr or "")
    if rollout_proc.returncode != 0:
        print(
            f"[WARN] deploy/{cfg.deployment} did not fully roll out in {cfg.rollout_timeout}; continuing.",
            file=sys.stderr,
        )

    ensure_script = cfg.root_dir / "scripts" / "ensure-qbit-credentials.sh"
    if not ensure_script.is_file():
        raise ConfigError(f"Missing ensure script: {ensure_script}")
    print("[INFO] Reconciling credentials from secret using ensure-qbit-credentials.sh")
    env = dict(os.environ)
    env["QBIT_API_VALIDATION"] = "0"
    env["QBIT_STRICT_LOGIN_CHECK"] = "0"
    proc = run_command(
        ["bash", str(ensure_script)],
        check=False,
        env=env,
    )
    sys.stdout.write(proc.stdout or "")
    sys.stderr.write(proc.stderr or "")
    if proc.returncode != 0:
        raise MediaStackError("ensure-qbit-credentials.sh failed after reset.")

    print("[OK] qBittorrent WebUI auth reset + reconciliation complete.")
    print("[OK] Try logging in at http://qbittorrent.local with your secret credentials.")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return run(parse_config(argv))
    except (ConfigError, MediaStackError, OSError, ValueError) as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
