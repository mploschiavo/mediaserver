#!/usr/bin/env python3
"""Reconcile Jellyfin home rails via bootstrap logic."""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib import error, request

from media_stack.cli.workflows.cli_common import repo_root_from_script_file

from media_stack.services.top_level_config_model import TopLevelBootstrapConfig


class ReconcileJellyfinHomeRailsMain:

    @staticmethod
    def _ts() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S%z")

    @staticmethod
    def _info(message: str) -> None:
        print(f"[{_ts()}] [INFO] {message}")

    @staticmethod
    def _warn(message: str) -> None:
        print(f"[{_ts()}] [WARN] {message}", file=sys.stderr)

    @staticmethod
    def _err(message: str) -> None:
        print(f"[{_ts()}] [ERR] {message}", file=sys.stderr)

    @staticmethod
    def _parse_args(root_dir: Path) -> argparse.Namespace:
        parser = argparse.ArgumentParser(
            description="Reconcile Jellyfin home rails using bootstrap config",
        )
        parser.add_argument(
            "config_file",
            nargs="?",
            default=str(root_dir / "contracts" / "media-stack.config.json"),
            help="Path to bootstrap config JSON",
        )
        parser.add_argument(
            "--force-enable",
            action="store_true",
            help="Force-enable jellyfin_home_rails before reconcile",
        )
        parser.add_argument("--namespace", default=os.environ.get("NAMESPACE", "media-stack"))
        parser.add_argument(
            "--local-port", type=int, default=int(os.environ.get("LOCAL_PORT", "18096"))
        )
        return parser.parse_args()

    @staticmethod
    def _choose_kubectl() -> list[str]:
        if shutil.which("microk8s"):
            return ["microk8s", "kubectl"]
        if shutil.which("kubectl"):
            return ["kubectl"]
        raise RuntimeError("kubectl not found in PATH.")

    @staticmethod
    def _read_secret_jellyfin_api_key(kubectl: list[str], namespace: str) -> str:
        result = subprocess.run(
            [
                *kubectl,
                "-n",
                namespace,
                "get",
                "secret",
                "media-stack-secrets",
                "-o",
                "jsonpath={.data.JELLYFIN_API_KEY}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Failed reading media-stack-secrets")
        encoded = result.stdout.strip()
        if not encoded:
            return ""
        decode = subprocess.run(
            ["bash", "-lc", f"printf '%s' '{encoded}' | base64 -d"],
            capture_output=True,
            text=True,
            check=False,
        )
        if decode.returncode != 0:
            raise RuntimeError(decode.stderr.strip() or "Failed decoding JELLYFIN_API_KEY")
        return decode.stdout.strip()

    @staticmethod
    def _wait_http_ok(url: str, timeout_seconds: int = 30) -> bool:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                with request.urlopen(url, timeout=3) as resp:
                    if 200 <= resp.status < 500:
                        return True
            except (error.URLError, TimeoutError):
                pass
            time.sleep(1)
        return False

    @staticmethod
    def _load_jellyfin_runtime_module(root_dir: Path):
        sys.path.insert(0, str(root_dir / "bin"))
        import media_stack.services.apps.jellyfin.runtime_ops as runtime_ops

        return runtime_ops

    def main(self) -> int:
        root_dir = repo_root_from_script_file(__file__)
        args = _parse_args(root_dir)
        config_path = Path(args.config_file).resolve()
        if not config_path.exists():
            _err(f"Config file not found: {config_path}")
            return 1

        try:
            kubectl = _choose_kubectl()
        except RuntimeError as exc:
            _err(str(exc))
            return 1

        _info(f"Namespace: {args.namespace}")
        _info(f"Config: {config_path}")

        try:
            jellyfin_api_key = _read_secret_jellyfin_api_key(kubectl, args.namespace)
        except RuntimeError as exc:
            _err(str(exc))
            return 1
        if not jellyfin_api_key:
            _err(f"Could not read JELLYFIN_API_KEY from secret {args.namespace}/media-stack-secrets.")
            return 1
        os.environ["JELLYFIN_API_KEY"] = jellyfin_api_key

        with tempfile.NamedTemporaryFile(prefix="media-stack-jf-pf.", delete=False) as pf_log:
            pf_log_path = Path(pf_log.name)

        _info(f"Starting port-forward on 127.0.0.1:{args.local_port} -> svc/jellyfin:8096")
        proc = subprocess.Popen(
            [
                *kubectl,
                "-n",
                args.namespace,
                "port-forward",
                "svc/jellyfin",
                f"{args.local_port}:8096",
            ],
            stdout=pf_log_path.open("w"),
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            if not _wait_http_ok(
                f"http://127.0.0.1:{args.local_port}/System/Info/Public", timeout_seconds=30
            ):
                _err("Could not reach Jellyfin through local port-forward.")
                _warn("port-forward logs:")
                try:
                    for line in pf_log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[
                        :120
                    ]:
                        print(line, file=sys.stderr)
                except Exception:
                    pass
                return 1

            _info("Reconciling Jellyfin home rails via bootstrap logic")
            raw_cfg = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(raw_cfg, dict):
                _err("Bootstrap config root must be a JSON object.")
                return 1
            try:
                cfg = TopLevelBootstrapConfig.from_dict(raw_cfg).to_dict()
            except ValueError as exc:
                _err(f"Invalid bootstrap config: {exc}")
                return 1
            rails_cfg = copy.deepcopy(cfg.get("jellyfin_home_rails") or {})
            if args.force_enable:
                rails_cfg["enabled"] = True
            rails_cfg["url"] = f"http://127.0.0.1:{args.local_port}"
            rails_cfg["api_key_env"] = "JELLYFIN_API_KEY"
            rails_cfg["auto_discover_api_key_from_db"] = False
            rails_cfg["auto_discover_user_id"] = True
            cfg["jellyfin_home_rails"] = rails_cfg

            module = _load_jellyfin_runtime_module(root_dir)
            module.ensure_jellyfin_home_rails(cfg, str(root_dir), 180)
            print("Jellyfin home rails reconcile complete.")
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            try:
                pf_log_path.unlink(missing_ok=True)
            except Exception:
                pass

        if args.force_enable:
            _info("Done (forced enable). Hard-refresh Jellyfin and re-open Collections.")
        else:
            _info("Done. Hard-refresh Jellyfin and re-open Movies/Home.")
        return 0


_instance = ReconcileJellyfinHomeRailsMain()
main = _instance.main


if __name__ == "__main__":
    raise SystemExit(main())
