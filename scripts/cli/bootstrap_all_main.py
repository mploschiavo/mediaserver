#!/usr/bin/env python3
"""Python bootstrap-all orchestration entrypoint.

Media Automation Stack by Matthew Loschiavo:
https://matthewloschiavo.com
"""

from __future__ import annotations

import argparse
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

from cli.bootstrap_component_resolver import (
    BootstrapComponentPlan,
    canonicalize_technology,
    resolve_bootstrap_component_plan,
    resolve_bootstrap_enable_workers,
    resolve_runner_phase_script,
    resolve_worker_deployment_name,
    resolve_worker_manifest_path,
)


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
        self._plan: BootstrapComponentPlan | None = None
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

    def _component_plan(self) -> BootstrapComponentPlan:
        if self._plan is None:
            self._plan = resolve_bootstrap_component_plan(self.cfg.config_file)
        return self._plan

    def _role_binding(self, role_key: str) -> str:
        return str(self._component_plan().role_bindings.get(role_key) or "").strip()

    def _selected_download_client(self, role_key: str) -> dict[str, object]:
        technology = self._role_binding(role_key)
        selected = self._component_plan().download_clients.get(technology)
        if isinstance(selected, dict):
            return selected
        return {}

    def _should_run_torrent_client_ensure(self) -> bool:
        selected = self._selected_download_client("torrent_client")
        return bool(
            selected.get("configure_arr_clients")
            or selected.get("set_categories_in_qbit")
            or selected.get("set_categories")
        )

    def _should_run_usenet_client_ensure(self) -> bool:
        selected = self._selected_download_client("usenet_client")
        return bool(selected.get("configure_arr_clients"))

    def _phase_script(self, phase_key: str, technology: str) -> str:
        return resolve_runner_phase_script(
            self._component_plan().config,
            phase_key=phase_key,
            technology=technology,
            aliases=self._component_plan().aliases,
        )

    def _manifest_overrides(self, text: str) -> str:
        out = re.sub(
            r"namespace:\s*media-stack\b",
            f"namespace: {self.cfg.namespace}",
            text,
        )
        out = re.sub(
            r"name:\s*media-stack\s*$", f"name: {self.cfg.namespace}", out, flags=re.MULTILINE
        )
        out = out.replace("/srv/media-stack", self.cfg.prepare_host_root)
        return out

    def _apply_manifest_file(self, manifest_path: Path, *, worker: str) -> None:
        if not manifest_path.is_file():
            raise ConfigError(f"Worker manifest not found for '{worker}': {manifest_path}")
        patched_text = self._manifest_overrides(manifest_path.read_text(encoding="utf-8"))
        from tempfile import TemporaryDirectory

        prefix_worker = re.sub(r"[^a-z0-9-]+", "-", str(worker or "").lower()).strip("-")
        prefix_worker = prefix_worker or "worker"
        with TemporaryDirectory(prefix=f"media-stack-{prefix_worker}-") as tmp:
            patched = Path(tmp) / manifest_path.name
            patched.write_text(patched_text, encoding="utf-8")
            result = self.kube.run(["apply", "-f", str(patched)], check=False)
            if result.stdout.strip():
                print(result.stdout.rstrip())
            if result.stderr.strip():
                print(result.stderr.rstrip(), file=sys.stderr)
            if result.returncode != 0:
                raise KubernetesError(result.stderr or result.stdout)

    def _enable_worker_deployment(self, worker: str) -> None:
        plan = self._component_plan()
        worker_manifest = resolve_worker_manifest_path(
            plan.config,
            worker=worker,
            aliases=plan.aliases,
        )
        manifest_path = (self.cfg.root_dir / worker_manifest).resolve()
        if not manifest_path.is_file():
            warn(
                f"Worker manifest not found for '{worker}' at {manifest_path}; "
                "skipping worker enable."
            )
            return

        deployment_name = resolve_worker_deployment_name(
            plan.config,
            worker=worker,
            aliases=plan.aliases,
            default=worker,
        )
        self._apply_manifest_file(manifest_path, worker=worker)
        self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "scale",
                f"deploy/{deployment_name}",
                "--replicas=1",
            ]
        )
        self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "rollout",
                "status",
                f"deploy/{deployment_name}",
                "--timeout=10m",
            ]
        )

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

        plan = self._component_plan()
        torrent_client = self._role_binding("torrent_client")
        usenet_client = self._role_binding("usenet_client")
        media_server = self._role_binding("media_server")
        request_manager = self._role_binding("request_manager")
        prowlarr_key = canonicalize_technology("prowlarr", plan.aliases) or "prowlarr"

        torrent_script = self._phase_script("torrent_client_credentials", torrent_client)
        usenet_script = self._phase_script("usenet_client_api_access", usenet_client)
        media_server_script = self._phase_script("media_server_bootstrap", media_server)
        request_seed_script = self._phase_script(
            "request_manager_seed_local_admin",
            request_manager,
        )
        indexer_script = self._phase_script("indexer_auto_discovery", prowlarr_key)

        should_run_torrent_ensure = self._should_run_torrent_client_ensure()
        should_run_usenet_ensure = self._should_run_usenet_client_ensure()
        should_run_indexer_discovery = bool(str(plan.config.get("prowlarr_url") or "").strip())

        if should_run_torrent_ensure and not torrent_script:
            warn(
                f"No runner phase script configured for torrent_client_credentials/{torrent_client}; "
                "skipping torrent-client ensure."
            )
        if should_run_usenet_ensure and not usenet_script:
            warn(
                f"No runner phase script configured for usenet_client_api_access/{usenet_client}; "
                "skipping usenet-client ensure."
            )
        if media_server and not media_server_script:
            warn(
                f"No runner phase script configured for media_server_bootstrap/{media_server}; "
                "skipping media-server bootstrap ensure."
            )

        self._run_phase(
            f"Ensure torrent client bootstrap access ({torrent_client or 'unbound'})",
            lambda: self._run_script(
                torrent_script,
                env={
                    "NAMESPACE": self.cfg.namespace,
                    "PREPARE_HOST_ROOT": self.cfg.prepare_host_root,
                },
            ),
            enabled=(
                not self.cfg.skip_qbit_ensure and should_run_torrent_ensure and bool(torrent_script)
            ),
        )

        self._run_phase(
            f"Ensure media server bootstrap access ({media_server or 'unbound'})",
            lambda: self._run_script(
                media_server_script,
                env={
                    "NAMESPACE": self.cfg.namespace,
                    "SECRET_NAME": self.cfg.secret_name,
                },
            ),
            enabled=(not self.cfg.skip_jellyfin_bootstrap and bool(media_server_script)),
        )

        self._run_phase(
            f"Ensure usenet client API access ({usenet_client or 'unbound'})",
            lambda: self._run_script(
                usenet_script,
                env={"NAMESPACE": self.cfg.namespace},
            ),
            enabled=(
                not self.cfg.skip_sab_ensure and should_run_usenet_ensure and bool(usenet_script)
            ),
        )

        self._run_phase(
            "Run bootstrap job",
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
            f"Seed request manager local admin ({request_manager or 'unbound'})",
            lambda: self._run_script(
                request_seed_script,
                env={"NAMESPACE": self.cfg.namespace},
            ),
            enabled=bool(request_seed_script),
        )

        self._run_phase(
            "Run indexer auto-discovery",
            lambda: self._run_script(
                indexer_script,
                env={
                    "NAMESPACE": self.cfg.namespace,
                    "PREPARE_HOST_ROOT": self.cfg.prepare_host_root,
                },
            ),
            enabled=(should_run_indexer_discovery and bool(indexer_script)),
        )

        workers_to_enable: tuple[str, ...] = ()
        if self.cfg.enable_unpackerr:
            workers_to_enable = resolve_bootstrap_enable_workers(
                plan.config,
                aliases=plan.aliases,
                fallback_workers=plan.worker_apps,
            )
            if not workers_to_enable:
                warn(
                    "No bootstrap workers configured in adapter_hooks.bootstrap_all.enable_workers; "
                    "worker enable phase skipped."
                )

        for worker in workers_to_enable:
            worker_key_sync_script = self._phase_script("worker_key_sync", worker)
            self._run_phase(
                f"Sync worker integration keys ({worker})",
                lambda script=worker_key_sync_script: self._run_script(
                    script,
                    env={"NAMESPACE": self.cfg.namespace},
                ),
                enabled=bool(worker_key_sync_script),
            )
            self._run_phase(
                f"Enable worker deployment ({worker})",
                lambda app=worker: self._enable_worker_deployment(app),
                enabled=True,
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
        help="Enable configured bootstrap worker deployments (legacy flag name).",
    )
    parser.add_argument(
        "--skip-qbit-ensure",
        action="store_true",
        default=str(os.environ.get("SKIP_QBIT_ENSURE", "0")).strip() == "1",
        help="Skip torrent client credential ensure phase (legacy flag name).",
    )
    parser.add_argument(
        "--skip-sab-ensure",
        action="store_true",
        default=str(os.environ.get("SKIP_SAB_ENSURE", "0")).strip() == "1",
        help="Skip usenet client API-access ensure phase (legacy flag name).",
    )
    parser.add_argument(
        "--skip-jellyfin-bootstrap",
        action="store_true",
        default=str(os.environ.get("SKIP_JELLYFIN_BOOTSTRAP", "0")).strip() == "1",
        help="Skip media-server bootstrap ensure phase (legacy flag name).",
    )
    parser.add_argument(
        "--resume",
        dest="resume",
        action="store_true",
        default=str(os.environ.get("BOOTSTRAP_RESUME", "1")).strip().lower()
        in {"1", "true", "yes", "on"},
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
