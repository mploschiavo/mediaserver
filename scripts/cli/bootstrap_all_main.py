#!/usr/bin/env python3
"""Python bootstrap-all orchestration entrypoint.

Media Automation Stack by Matthew Loschiavo:
https://matthewloschiavo.com
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from core.exceptions import ConfigError, KubernetesError
from core.kube import KubectlClient
from core.state_store import CheckpointStateStore


def ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def info(message: str) -> None:
    print(f"[{ts()}] [INFO] {message}", flush=True)


def warn(message: str) -> None:
    print(f"[{ts()}] [WARN] {message}", file=sys.stderr, flush=True)


def err(message: str) -> None:
    print(f"[{ts()}] [ERR] {message}", file=sys.stderr, flush=True)


@dataclass
class PhaseTracker:
    run_start_epoch: int = field(default_factory=lambda: int(time.time()))
    current_phase: str = ""
    current_start: int = 0
    names: list[str] = field(default_factory=list)
    results: list[str] = field(default_factory=list)
    seconds: list[int] = field(default_factory=list)

    def start(self, phase_name: str) -> None:
        self.current_phase = phase_name
        self.current_start = int(time.time())
        info(f"[PHASE] START: {phase_name}")

    def end(self, result: str) -> None:
        now = int(time.time())
        if self.current_phase:
            elapsed = now - self.current_start
            self.names.append(self.current_phase)
            self.results.append(result)
            self.seconds.append(elapsed)
            if result == "ok":
                info(f"[PHASE] DONE: {self.current_phase} ({elapsed}s)")
            elif result == "skipped":
                info(f"[PHASE] SKIP: {self.current_phase} ({elapsed}s)")
            else:
                warn(f"[PHASE] FAIL: {self.current_phase} ({elapsed}s)")
        self.current_phase = ""
        self.current_start = 0

    def summary(self) -> None:
        total = int(time.time()) - self.run_start_epoch
        info(f"Phase Summary (total {total}s)")
        if not self.names:
            info("  (no phases recorded)")
            return
        for idx, name in enumerate(self.names):
            info(f"  {name} => {self.results[idx]} ({self.seconds[idx]}s)")


@dataclass(frozen=True)
class BootstrapAllConfig:
    root_dir: Path
    config_file: Path
    namespace: str
    enable_unpackerr: bool
    secret_name: str
    prepare_host_root: str
    skip_qbit_ensure: bool
    skip_sab_ensure: bool
    skip_jellyfin_bootstrap: bool
    resume: bool
    state_file: Path


class BootstrapAllRunner:
    def __init__(self, cfg: BootstrapAllConfig) -> None:
        self.cfg = cfg
        self.kube = KubectlClient.from_environment()
        self.tracker = PhaseTracker()
        self.state = CheckpointStateStore(cfg.state_file)
        self.state.load()

    def _run_script(self, script_name: str, *args: str, env: dict[str, str] | None = None) -> None:
        script_path = self.cfg.root_dir / "scripts" / script_name
        call_env = dict(os.environ)
        if env:
            call_env.update({k: str(v) for k, v in env.items()})
        proc = subprocess.run(
            ["bash", str(script_path), *list(args)],
            cwd=str(self.cfg.root_dir),
            env=call_env,
            check=False,
            text=True,
            capture_output=True,
        )
        if proc.stdout.strip():
            print(proc.stdout.rstrip())
        if proc.stderr.strip():
            print(proc.stderr.rstrip(), file=sys.stderr)
        if proc.returncode != 0:
            raise RuntimeError(
                f"{script_name} failed ({proc.returncode}): "
                f"{' '.join(shlex.quote(x) for x in [str(script_path), *args])}"
            )

    def _config_probe(self, probe: str) -> bool:
        cfg = json.loads(self.cfg.config_file.read_text(encoding="utf-8"))
        bindings = cfg.get("technology_bindings") or {}
        clients = cfg.get("download_clients") or {}
        if not isinstance(bindings, dict):
            bindings = {}
        if not isinstance(clients, dict):
            clients = {}

        def resolve_client(role_key: str, default_key: str) -> dict:
            selected = str(bindings.get(role_key, default_key) or "").strip().lower() or default_key
            selected_cfg = clients.get(selected)
            if isinstance(selected_cfg, dict):
                return selected_cfg
            fallback = clients.get(default_key)
            return fallback if isinstance(fallback, dict) else {}

        if probe == "torrent-ensure":
            client = resolve_client("torrent_client", "qbittorrent")
            return bool(
                client.get("configure_arr_clients")
                or client.get("set_categories_in_qbit")
                or client.get("set_categories")
            )
        if probe == "usenet-ensure":
            client = resolve_client("usenet_client", "sabnzbd")
            return bool(client.get("configure_arr_clients"))
        raise ConfigError(f"Unknown config probe: {probe}")

    def _manifest_overrides(self, text: str) -> str:
        out = re.sub(
            r"namespace:\s*media-stack\b",
            f"namespace: {self.cfg.namespace}",
            text,
        )
        out = re.sub(r"name:\s*media-stack\s*$", f"name: {self.cfg.namespace}", out, flags=re.MULTILINE)
        out = out.replace("/srv/media-stack", self.cfg.prepare_host_root)
        return out

    def _apply_unpacked_manifest(self) -> None:
        manifest_path = self.cfg.root_dir / "k8s" / "unpackerr.yaml"
        patched_text = self._manifest_overrides(manifest_path.read_text(encoding="utf-8"))
        from tempfile import TemporaryDirectory

        with TemporaryDirectory(prefix="media-stack-unpackerr-") as tmp:
            patched = Path(tmp) / "unpackerr.yaml"
            patched.write_text(patched_text, encoding="utf-8")
            result = self.kube.run(["apply", "-f", str(patched)], check=False)
            if result.stdout.strip():
                print(result.stdout.rstrip())
            if result.stderr.strip():
                print(result.stderr.rstrip(), file=sys.stderr)
            if result.returncode != 0:
                raise KubernetesError(result.stderr or result.stdout)

    def _run_phase(
        self,
        name: str,
        action: Callable[[], None],
        *,
        enabled: bool = True,
    ) -> None:
        self.tracker.start(name)
        if not enabled:
            self.tracker.end("skipped")
            self.state.mark_phase(name, "skipped")
            return
        if self.cfg.resume and self.state.is_phase_done(name):
            info(f"Resume checkpoint: skipping already-completed phase '{name}'.")
            self.tracker.end("skipped")
            return
        try:
            action()
            self.tracker.end("ok")
            self.state.mark_phase(name, "ok")
        except Exception as exc:
            self.tracker.end("failed")
            self.state.mark_phase(name, "failed", error=str(exc))
            raise

    def run(self) -> int:
        if not self.cfg.config_file.exists():
            raise ConfigError(f"Config file not found: {self.cfg.config_file}")

        should_run_qbit = self._config_probe("torrent-ensure")
        should_run_sab = self._config_probe("usenet-ensure")

        self._run_phase(
            "Ensure qBittorrent credentials",
            lambda: self._run_script(
                "ensure-qbit-credentials.sh",
                env={
                    "NAMESPACE": self.cfg.namespace,
                    "PREPARE_HOST_ROOT": self.cfg.prepare_host_root,
                },
            ),
            enabled=(not self.cfg.skip_qbit_ensure and should_run_qbit),
        )

        self._run_phase(
            "Ensure Jellyfin bootstrap and API key",
            lambda: self._run_script(
                "ensure-jellyfin-bootstrap.sh",
                env={
                    "NAMESPACE": self.cfg.namespace,
                    "SECRET_NAME": self.cfg.secret_name,
                },
            ),
            enabled=(not self.cfg.skip_jellyfin_bootstrap),
        )

        self._run_phase(
            "Ensure SABnzbd API access",
            lambda: self._run_script(
                "ensure-sabnzbd-api-access.sh",
                env={"NAMESPACE": self.cfg.namespace},
            ),
            enabled=(not self.cfg.skip_sab_ensure and should_run_sab),
        )

        self._run_phase(
            "Run Arr/Prowlarr/Jellyseerr bootstrap job",
            lambda: self._run_script(
                "run-bootstrap-job.sh",
                str(self.cfg.config_file),
                env={
                    "NAMESPACE": self.cfg.namespace,
                    "SKIP_QBIT_ENSURE": "1",
                    "SKIP_SAB_ENSURE": "1",
                    "PREPARE_HOST_ROOT": self.cfg.prepare_host_root,
                },
            ),
        )

        self._run_phase(
            "Seed Jellyseerr local admin",
            lambda: self._run_script(
                "seed-jellyseerr-local-admin.sh",
                env={"NAMESPACE": self.cfg.namespace},
            ),
        )

        self._run_phase(
            "Run Prowlarr auto-indexer discovery",
            lambda: self._run_script(
                "run-prowlarr-auto-indexers.sh",
                env={
                    "NAMESPACE": self.cfg.namespace,
                    "PREPARE_HOST_ROOT": self.cfg.prepare_host_root,
                },
            ),
        )

        self._run_phase(
            "Sync Unpackerr API keys",
            lambda: self._run_script(
                "sync-unpackerr-keys.sh",
                env={"NAMESPACE": self.cfg.namespace},
            ),
        )

        def enable_unpackerr() -> None:
            self._apply_unpacked_manifest()
            self.kube.run(
                [
                    "-n",
                    self.cfg.namespace,
                    "scale",
                    "deploy/unpackerr",
                    "--replicas=1",
                ]
            )
            self.kube.run(
                [
                    "-n",
                    self.cfg.namespace,
                    "rollout",
                    "status",
                    "deploy/unpackerr",
                    "--timeout=10m",
                ]
            )

        self._run_phase(
            "Enable Unpackerr deployment",
            enable_unpackerr,
            enabled=self.cfg.enable_unpackerr,
        )

        info("Full bootstrap complete.")
        self.tracker.summary()
        return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    root_dir = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        prog="scripts/bootstrap-all.sh",
        description="Python bootstrap-all orchestration runner",
    )
    parser.add_argument(
        "config_file",
        nargs="?",
        default=str(root_dir / "bootstrap" / "media-stack.bootstrap.json"),
        help="Bootstrap config JSON path",
    )
    parser.add_argument("--namespace", default=os.environ.get("NAMESPACE", "media-stack"))
    parser.add_argument(
        "--secret-name",
        default=os.environ.get("SECRET_NAME", "media-stack-secrets"),
    )
    parser.add_argument(
        "--prepare-host-root",
        default=os.environ.get("PREPARE_HOST_ROOT", "/srv/media-stack"),
    )
    parser.add_argument(
        "--enable-unpackerr",
        action="store_true",
        default=str(os.environ.get("ENABLE_UNPACKERR", "1")).strip() == "1",
    )
    parser.add_argument(
        "--skip-qbit-ensure",
        action="store_true",
        default=str(os.environ.get("SKIP_QBIT_ENSURE", "0")).strip() == "1",
    )
    parser.add_argument(
        "--skip-sab-ensure",
        action="store_true",
        default=str(os.environ.get("SKIP_SAB_ENSURE", "0")).strip() == "1",
    )
    parser.add_argument(
        "--skip-jellyfin-bootstrap",
        action="store_true",
        default=str(os.environ.get("SKIP_JELLYFIN_BOOTSTRAP", "0")).strip() == "1",
    )
    parser.add_argument(
        "--resume",
        dest="resume",
        action="store_true",
        default=str(os.environ.get("BOOTSTRAP_RESUME", "1")).strip().lower() in {"1", "true", "yes", "on"},
        help="Resume from completed phase checkpoints (default: enabled).",
    )
    parser.add_argument(
        "--no-resume",
        dest="resume",
        action="store_false",
        help="Ignore previous phase checkpoints and run all phases.",
    )
    parser.add_argument(
        "--state-file",
        default=os.environ.get("BOOTSTRAP_STATE_FILE", ""),
        help="Checkpoint state file path (default: .state/bootstrap-all-<namespace>.json).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root_dir = Path(__file__).resolve().parents[2]
    config_file = Path(str(args.config_file)).resolve()
    state_file = (
        Path(args.state_file).resolve()
        if str(args.state_file).strip()
        else root_dir / ".state" / f"bootstrap-all-{args.namespace}.json"
    )
    cfg = BootstrapAllConfig(
        root_dir=root_dir,
        config_file=config_file,
        namespace=str(args.namespace).strip(),
        enable_unpackerr=bool(args.enable_unpackerr),
        secret_name=str(args.secret_name).strip(),
        prepare_host_root=str(args.prepare_host_root).strip(),
        skip_qbit_ensure=bool(args.skip_qbit_ensure),
        skip_sab_ensure=bool(args.skip_sab_ensure),
        skip_jellyfin_bootstrap=bool(args.skip_jellyfin_bootstrap),
        resume=bool(args.resume),
        state_file=state_file,
    )
    runner = BootstrapAllRunner(cfg)
    try:
        return runner.run()
    except (ConfigError, KubernetesError, RuntimeError) as exc:
        err(str(exc))
        runner.tracker.summary()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
