#!/usr/bin/env python3
"""Run the full media-stack bootstrap Kubernetes job."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Callable

from core.exceptions import ConfigError, KubernetesError, MediaStackError
from core.kube import KubectlClient

from cli.bootstrap_config_resolver_service import (
    BootstrapConfigResolverConfig,
    BootstrapConfigResolverService,
)
from cli.bootstrap_job_wait_service import BootstrapJobWaitConfig, BootstrapJobWaitService
from cli.bootstrap_manifest_service import BootstrapManifestConfig, BootstrapManifestService
from cli.bootstrap_secret_priming_service import (
    BootstrapSecretPrimingConfig,
    BootstrapSecretPrimingService,
)
from cli.jellyfin_plugin_activation_service import (
    JellyfinPluginActivationConfig,
    JellyfinPluginActivationService,
)


def ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def info(message: str) -> None:
    print(f"[{ts()}] [INFO] {message}", flush=True)


def warn(message: str) -> None:
    print(f"[{ts()}] [WARN] {message}", file=sys.stderr, flush=True)


def err(message: str) -> None:
    print(f"[{ts()}] [ERR] {message}", file=sys.stderr, flush=True)


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class RunBootstrapJobConfig:
    namespace: str
    timeout_raw: str
    heartbeat_interval: int
    job_log_tail_lines: int
    alert_webhook_url: str
    prepare_host_root: str
    ingress_name: str
    bootstrap_runner_image: str
    root_dir: Path
    config_file: Path
    skip_qbit_ensure: bool
    skip_sab_ensure: bool

    @property
    def timeout_seconds(self) -> int:
        raw = self.timeout_raw.strip()
        match = re.match(r"^(\d+)([smh]?)$", raw)
        if not match:
            return 600
        num = int(match.group(1))
        unit = match.group(2)
        if unit == "h":
            return num * 3600
        if unit in ("m", ""):
            return num * 60
        if unit == "s":
            return num
        return 600


@dataclass
class PhaseTracker:
    run_start_epoch: int = field(default_factory=lambda: int(time.time()))
    current_phase: str = ""
    current_phase_start: int = 0
    phase_names: list[str] = field(default_factory=list)
    phase_results: list[str] = field(default_factory=list)
    phase_seconds: list[int] = field(default_factory=list)

    def start(self, phase_name: str) -> None:
        self.current_phase = phase_name
        self.current_phase_start = int(time.time())
        info(f"[PHASE] START: {phase_name}")

    def end(self, result: str = "ok") -> None:
        now = int(time.time())
        if self.current_phase and self.current_phase_start > 0:
            elapsed = now - self.current_phase_start
            self.phase_names.append(self.current_phase)
            self.phase_results.append(result)
            self.phase_seconds.append(elapsed)
            if result == "ok":
                info(f"[PHASE] DONE: {self.current_phase} ({elapsed}s)")
            elif result == "skipped":
                info(f"[PHASE] SKIP: {self.current_phase} ({elapsed}s)")
            else:
                warn(f"[PHASE] FAIL: {self.current_phase} ({elapsed}s)")
        self.current_phase = ""
        self.current_phase_start = 0

    def print_summary(self) -> None:
        total = int(time.time()) - self.run_start_epoch
        info(f"Phase Summary (total {total}s)")
        if not self.phase_names:
            info("  (no phases recorded)")
            return
        for idx, name in enumerate(self.phase_names):
            info(f"  {name} => {self.phase_results[idx]} ({self.phase_seconds[idx]}s)")


class RunBootstrapJobRunner:
    def __init__(self, cfg: RunBootstrapJobConfig, kube: KubectlClient, tracker: PhaseTracker) -> None:
        self.cfg = cfg
        self.kube = kube
        self.tracker = tracker
        self.job_log_file = Path(
            NamedTemporaryFile(
                prefix="media-stack-bootstrap-log.",
                suffix=".log",
                delete=False,
            ).name
        )
        self.job_config_file = Path(
            NamedTemporaryFile(
                prefix="media-stack-bootstrap-config.",
                suffix=".json",
                delete=False,
            ).name
        )

    def _job_wait_service(self) -> BootstrapJobWaitService:
        return BootstrapJobWaitService(
            cfg=BootstrapJobWaitConfig(
                namespace=self.cfg.namespace,
                timeout_seconds=self.cfg.timeout_seconds,
                timeout_raw=self.cfg.timeout_raw,
                heartbeat_interval=self.cfg.heartbeat_interval,
            ),
            kube=self.kube,
            info=info,
            warn=warn,
        )

    def _secret_priming_service(self) -> BootstrapSecretPrimingService:
        return BootstrapSecretPrimingService(
            cfg=BootstrapSecretPrimingConfig(namespace=self.cfg.namespace),
            kube=self.kube,
            info=info,
            warn=warn,
        )

    def _manifest_service(self) -> BootstrapManifestService:
        return BootstrapManifestService(
            cfg=BootstrapManifestConfig(
                namespace=self.cfg.namespace,
                root_dir=self.cfg.root_dir,
                prepare_host_root=self.cfg.prepare_host_root,
                bootstrap_runner_image=self.cfg.bootstrap_runner_image,
                job_config_file=self.job_config_file,
            ),
            kube=self.kube,
            info=info,
            warn=warn,
        )

    def _config_resolver_service(self) -> BootstrapConfigResolverService:
        return BootstrapConfigResolverService(
            cfg=BootstrapConfigResolverConfig(
                namespace=self.cfg.namespace,
                ingress_name=self.cfg.ingress_name,
                config_file=self.cfg.config_file,
                job_config_file=self.job_config_file,
            ),
            kube=self.kube,
            info=info,
        )

    def _jellyfin_plugin_service(self) -> JellyfinPluginActivationService:
        return JellyfinPluginActivationService(
            cfg=JellyfinPluginActivationConfig(namespace=self.cfg.namespace),
            kube=self.kube,
            info=info,
            warn=warn,
            deployment_exists=self.deployment_exists,
            restart_deployment=lambda deployment, timeout_seconds: self.restart_deployment(
                deployment,
                timeout_seconds=timeout_seconds,
            ),
            read_secret_key=self._read_secret_key,
        )

    def run(self) -> int:
        if not self.cfg.config_file.exists():
            raise ConfigError(f"Config file not found: {self.cfg.config_file}")

        info(f"Namespace: {self.cfg.namespace}")
        info(f"Config: {self.cfg.config_file}")
        info(f"Ingress: {self.cfg.ingress_name}")
        info(f"Bootstrap runner image: {self.cfg.bootstrap_runner_image}")
        info(f"Heartbeat interval: {self.cfg.heartbeat_interval}s")
        self.notify("info", f"media-stack bootstrap job started (namespace={self.cfg.namespace})")

        try:
            self._run_phase(
                "Ensure qBittorrent credentials",
                lambda: self._run_script(
                    "ensure-qbit-credentials.sh",
                    env={
                        "NAMESPACE": self.cfg.namespace,
                        "PREPARE_HOST_ROOT": self.cfg.prepare_host_root,
                    },
                ),
                enabled=not self.cfg.skip_qbit_ensure,
            )
            self._run_phase(
                "Ensure SABnzbd API access",
                lambda: self._run_script(
                    "ensure-sabnzbd-api-access.sh",
                    env={"NAMESPACE": self.cfg.namespace},
                ),
                enabled=not self.cfg.skip_sab_ensure,
            )
            self._run_phase("Resolve bootstrap config", self.resolve_bootstrap_config)
            self._run_phase("Ensure bootstrap PVC prerequisites", self.ensure_bootstrap_pvc_prereqs)
            self._run_phase("Prime Arr API keys into secret", self.prime_servarr_api_keys_secret)
            self._run_phase("Prime SAB API key into secret", self.prime_sab_api_key_secret)
            self._run_phase("Prime Jellyseerr API key into secret", self.prime_jellyseerr_api_key_secret)
            self._run_phase("Prime Tautulli API key into secret", self.prime_tautulli_api_key_secret)
            self._run_phase("Update bootstrap ConfigMaps", self.update_bootstrap_configmaps)
            self._run_phase("Recreate bootstrap Job", self.recreate_bootstrap_job)
            self._run_phase("Wait for bootstrap Job completion", self.wait_for_bootstrap_job)
            self._run_phase("Print bootstrap Job logs", self.print_bootstrap_job_logs)

            if self._log_contains("Jellyseerr: settings file bootstrap applied"):
                self._run_phase(
                    "Restart Jellyseerr after file bootstrap",
                    lambda: self.restart_deployment("jellyseerr", timeout_seconds=180),
                )

            if self._log_contains("Homepage: wrote services config"):
                self._run_phase(
                    "Restart Homepage after config sync",
                    lambda: self.restart_deployment_if_exists("homepage", timeout_seconds=180),
                )

            if self._log_contains("Bazarr: wrote integration config"):
                self._run_phase(
                    "Restart Bazarr after config sync",
                    lambda: self.restart_deployment_if_exists("bazarr", timeout_seconds=180),
                )

            self._run_phase(
                "Activate Jellyfin plugins (restart if needed)",
                self.activate_jellyfin_plugins,
            )

            info("Bootstrap job completed.")
            self.tracker.print_summary()
            self.notify("ok", f"media-stack bootstrap job completed (namespace={self.cfg.namespace})")
            return 0
        except Exception:
            self.notify("error", f"media-stack bootstrap job failed (namespace={self.cfg.namespace})")
            raise
        finally:
            self.cleanup()

    def _run_phase(self, phase_name: str, fn: Callable[[], None], *, enabled: bool = True) -> None:
        self.tracker.start(phase_name)
        if not enabled:
            self.tracker.end("skipped")
            return
        try:
            fn()
            self.tracker.end("ok")
        except Exception:
            self.tracker.end("failed")
            raise

    def cleanup(self) -> None:
        for file_path in (self.job_log_file, self.job_config_file):
            try:
                file_path.unlink(missing_ok=True)
            except Exception:
                pass

    def notify(self, status: str, message: str) -> None:
        if not self.cfg.alert_webhook_url:
            return
        payload = json.dumps({"status": status, "message": message}).encode("utf-8")
        request = urllib.request.Request(
            self.cfg.alert_webhook_url,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=8):
                return
        except urllib.error.URLError:
            return

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

    def manifest_overrides(self, text: str) -> str:
        return self._manifest_service().manifest_overrides(text)

    def ensure_bootstrap_pvc_prereqs(self) -> None:
        self._manifest_service().ensure_bootstrap_pvc_prereqs()

    def resolve_bootstrap_config(self) -> None:
        self._config_resolver_service().resolve_bootstrap_config()

    def prime_servarr_api_keys_secret(self) -> None:
        self._secret_priming_service().prime_servarr_api_keys()

    def prime_sab_api_key_secret(self) -> None:
        self._secret_priming_service().prime_sab_api_key()

    def prime_jellyseerr_api_key_secret(self) -> None:
        self._secret_priming_service().prime_jellyseerr_api_key()

    def prime_tautulli_api_key_secret(self) -> None:
        self._secret_priming_service().prime_tautulli_api_key()

    def update_bootstrap_configmaps(self) -> None:
        self._manifest_service().update_bootstrap_configmaps()

    def recreate_bootstrap_job(self) -> None:
        self._manifest_service().recreate_bootstrap_job()

    def wait_for_bootstrap_job(self) -> None:
        self._job_wait_service().wait_for_job(
            job_name="media-stack-bootstrap",
            selector="app=media-stack-bootstrap",
        )

    def print_bootstrap_job_logs(self) -> None:
        result = self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "logs",
                "job/media-stack-bootstrap",
                "--timestamps",
            ],
            check=False,
        )
        if result.returncode != 0:
            raise KubernetesError(result.stderr or result.stdout)
        self.job_log_file.write_text(result.stdout or "", encoding="utf-8")
        lines = (result.stdout or "").splitlines()
        tail = lines[-max(1, self.cfg.job_log_tail_lines) :]
        if tail:
            print("\n".join(tail))

    def _log_contains(self, marker: str) -> bool:
        if not self.job_log_file.exists():
            return False
        try:
            return marker in self.job_log_file.read_text(encoding="utf-8")
        except Exception:
            return False

    def deployment_exists(self, deployment: str) -> bool:
        result = self.kube.run(
            ["-n", self.cfg.namespace, "get", f"deploy/{deployment}"],
            check=False,
        )
        return result.returncode == 0

    def restart_deployment(self, deployment: str, *, timeout_seconds: int) -> None:
        info(f"Restarting deployment/{deployment}.")
        restart = self.kube.run(
            ["-n", self.cfg.namespace, "rollout", "restart", f"deployment/{deployment}"],
            check=False,
        )
        if restart.returncode != 0:
            raise KubernetesError(restart.stderr or restart.stdout)
        status = self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "rollout",
                "status",
                f"deployment/{deployment}",
                f"--timeout={timeout_seconds}s",
            ],
            check=False,
        )
        if status.returncode != 0:
            raise KubernetesError(status.stderr or status.stdout)

    def restart_deployment_if_exists(self, deployment: str, *, timeout_seconds: int) -> None:
        if not self.deployment_exists(deployment):
            info(f"deployment/{deployment} not found in namespace/{self.cfg.namespace}; skipping restart.")
            return
        self.restart_deployment(deployment, timeout_seconds=timeout_seconds)

    def _read_secret_key(self, secret: str, key_name: str) -> str:
        result = self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "get",
                "secret",
                secret,
                f"-o=jsonpath={{.data.{key_name}}}",
            ],
            check=False,
        )
        if result.returncode != 0:
            return ""
        value = (result.stdout or "").strip()
        if not value:
            return ""
        try:
            return base64.b64decode(value).decode("utf-8")
        except Exception:
            return ""

    def activate_jellyfin_plugins(self) -> None:
        self._jellyfin_plugin_service().activate_plugins_if_needed()


def build_parser(root_dir: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run media-stack bootstrap job.\n\n"
            "Usage:\n"
            "  scripts/run-bootstrap-job.sh [CONFIG_FILE]"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "config_file",
        nargs="?",
        default=str(root_dir / "bootstrap" / "media-stack.bootstrap.json"),
        help="Bootstrap JSON file path.",
    )
    parser.add_argument(
        "--namespace",
        default=os.environ.get("NAMESPACE", "media-stack"),
        help="Kubernetes namespace (env: NAMESPACE).",
    )
    parser.add_argument(
        "--timeout",
        default=os.environ.get("TIMEOUT", "10m"),
        help="Wait timeout, e.g. 600s, 10m, 1h (env: TIMEOUT).",
    )
    parser.add_argument(
        "--heartbeat-interval",
        type=int,
        default=max(1, int(os.environ.get("HEARTBEAT_INTERVAL", "15"))),
        help="Heartbeat seconds while waiting for job completion.",
    )
    parser.add_argument(
        "--job-log-tail-lines",
        type=int,
        default=max(1, int(os.environ.get("JOB_LOG_TAIL_LINES", "120"))),
        help="Tail lines to print from bootstrap job logs.",
    )
    parser.add_argument(
        "--prepare-host-root",
        default=os.environ.get("PREPARE_HOST_ROOT", "/srv/media-stack"),
        help="Host root used in manifest overrides.",
    )
    parser.add_argument(
        "--ingress-name",
        default=os.environ.get("INGRESS_NAME", "media-stack-ingress"),
        help="Ingress to read hosts from.",
    )
    parser.add_argument(
        "--bootstrap-runner-image",
        default=os.environ.get(
            "BOOTSTRAP_RUNNER_IMAGE",
            "192.168.1.60:30002/library/media-stack-bootstrap-runner:latest",
        ),
        help="Bootstrap runner container image.",
    )
    parser.add_argument(
        "--alert-webhook-url",
        default=os.environ.get("ALERT_WEBHOOK_URL", ""),
        help="Optional webhook for status notifications.",
    )
    parser.add_argument(
        "--skip-qbit-ensure",
        action="store_true",
        default=env_bool("SKIP_QBIT_ENSURE", False),
        help="Skip qBittorrent ensure phase.",
    )
    parser.add_argument(
        "--skip-sab-ensure",
        action="store_true",
        default=env_bool("SKIP_SAB_ENSURE", False),
        help="Skip SABnzbd ensure phase.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    root_dir = Path(__file__).resolve().parents[2]
    parser = build_parser(root_dir)
    args = parser.parse_args(argv)

    cfg = RunBootstrapJobConfig(
        namespace=str(args.namespace).strip() or "media-stack",
        timeout_raw=str(args.timeout).strip() or "10m",
        heartbeat_interval=max(1, int(args.heartbeat_interval)),
        job_log_tail_lines=max(1, int(args.job_log_tail_lines)),
        alert_webhook_url=str(args.alert_webhook_url).strip(),
        prepare_host_root=str(args.prepare_host_root).strip() or "/srv/media-stack",
        ingress_name=str(args.ingress_name).strip() or "media-stack-ingress",
        bootstrap_runner_image=str(args.bootstrap_runner_image).strip()
        or "192.168.1.60:30002/library/media-stack-bootstrap-runner:latest",
        root_dir=root_dir,
        config_file=Path(str(args.config_file)),
        skip_qbit_ensure=bool(args.skip_qbit_ensure),
        skip_sab_ensure=bool(args.skip_sab_ensure),
    )

    runner = RunBootstrapJobRunner(cfg=cfg, kube=KubectlClient.from_environment(), tracker=PhaseTracker())
    return runner.run()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MediaStackError as exc:
        err(str(exc))
        raise SystemExit(1)
    except KeyboardInterrupt:
        err("Interrupted.")
        raise SystemExit(130)
