#!/usr/bin/env python3
"""Run Prowlarr auto-indexer discovery job with Kubernetes orchestration."""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from media_stack.core.defaults import default_controller_image
from media_stack.core.exceptions import ConfigError, KubernetesError
from media_stack.core.platforms.kubernetes.kube_client import KubernetesClient

# Module logger for diagnostic/kubectl pass-through output. The top-level
# info()/warn()/err() helpers below remain print-based because they emit
# user-facing CLI progress lines with wall-clock timestamps.
logger = logging.getLogger("prowlarr_auto_indexers")


def ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def info(message: str) -> None:
    print(f"[{ts()}] [INFO] {message}")


def warn(message: str) -> None:
    print(f"[{ts()}] [WARN] {message}", file=sys.stderr)


def err(message: str) -> None:
    print(f"[{ts()}] [ERR] {message}", file=sys.stderr)


@dataclass(frozen=True)
class AutoIndexerConfig:
    namespace: str
    timeout_raw: str
    heartbeat_interval: int
    prepare_host_root: str
    bootstrap_runner_image: str
    exclude_name_tokens: list[str]
    reputation_cfg: dict[str, Any]
    root_dir: Path

    @property
    def timeout_seconds(self) -> int:
        raw = self.timeout_raw.strip()
        match = re.match(r"^(\d+)([smh]?)$", raw)
        if not match:
            return 1200
        num = int(match.group(1))
        unit = match.group(2)
        if unit == "h":
            return num * 3600
        if unit in ("m", ""):
            return num * 60
        if unit == "s":
            return num
        return 1200


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
        for idx, phase_name in enumerate(self.phase_names):
            info(f"  {phase_name} => {self.phase_results[idx]} " f"({self.phase_seconds[idx]}s)")


class ProwlarrAutoIndexerRunner:
    def __init__(
        self,
        cfg: AutoIndexerConfig,
        kube: KubernetesClient,
        tracker: PhaseTracker,
    ) -> None:
        self.cfg = cfg
        self.kube = kube
        self.tracker = tracker

    def run(self) -> int:
        info(f"Namespace: {self.cfg.namespace}")
        info(f"Heartbeat interval: {self.cfg.heartbeat_interval}s")
        info(f"Host root: {self.cfg.prepare_host_root}")
        info(f"Bootstrap runner image: {self.cfg.bootstrap_runner_image}")

        self._run_phase("Ensure auto-indexer PVC prerequisites", self.ensure_pvc_prereqs)
        self._run_phase("Update auto-indexer config ConfigMap", self.update_auto_configmap)
        self._run_phase("Recreate auto-indexer Job", self.recreate_job)
        self._run_phase("Wait for auto-indexer Job completion", self.wait_for_job)
        self._run_phase("Print auto-indexer Job logs", self.print_job_logs)

        info("Auto indexer bootstrap complete.")
        self.tracker.print_summary()
        return 0

    def _run_phase(self, phase_name: str, fn) -> None:
        self.tracker.start(phase_name)
        try:
            fn()
            self.tracker.end("ok")
        except Exception:
            self.tracker.end("failed")
            raise

    def manifest_overrides(self, text: str) -> str:
        text = re.sub(r"namespace:\s*media-stack", f"namespace: {self.cfg.namespace}", text)
        text = text.replace("/srv/media-stack", self.cfg.prepare_host_root)
        text = text.replace(
            default_controller_image(),
            self.cfg.bootstrap_runner_image,
        )
        return text

    def ensure_pvc_prereqs(self) -> None:
        required_pvcs = [
            "media-stack-config-prowlarr",
        ]

        missing: list[str] = []
        for pvc in required_pvcs:
            result = self.kube.run(
                ["-n", self.cfg.namespace, "get", "pvc", pvc],
                check=False,
            )
            if result.returncode != 0:
                missing.append(pvc)

        if missing:
            warn(f"Missing required PVC(s) for auto-indexer job: {' '.join(missing)}")
            warn(
                "Provision/restore required PVC(s) and retry. "
                "You can apply storage defaults with: "
                f"kubectl apply -f {self.cfg.root_dir / 'k8s' / 'storage-pvc.yaml'}"
            )
            raise ConfigError("Missing required PVCs for auto-indexer job")

        info("Auto-indexer PVC prerequisites are present.")

    def _replace_or_create_from_yaml(self, yaml_path: Path, kind_name: str) -> None:
        result = self.kube.run(
            ["-n", self.cfg.namespace, "replace", "-f", str(yaml_path)],
            check=False,
        )
        if result.returncode == 0:
            info(f"{kind_name} replaced")
            return

        create = self.kube.run(
            ["-n", self.cfg.namespace, "create", "-f", str(yaml_path)],
            check=False,
        )
        if create.returncode != 0:
            raise KubernetesError(create.stderr or create.stdout)

    def _load_base_config(self) -> dict[str, Any]:
        """Load the full bootstrap config as a base for the auto-indexer.

        Uses the repo's bootstrap JSON directly rather than building a minimal
        payload by hand. This ensures the auto-indexer config always has the
        required sections (download_clients, adapter_hooks, etc.) that the
        bootstrap runtime factory expects.
        """
        config_file = self.cfg.root_dir / "contracts" / "media-stack.config.json"
        if config_file.exists():
            return json.loads(config_file.read_text(encoding="utf-8"))
        return {}

    def update_auto_configmap(self) -> None:
        info("Updating temporary bootstrap config ConfigMap")
        config_payload = self._load_base_config()
        # Override: enable auto-indexer discovery, disable everything else.
        config_payload["prowlarr_auto_add_tested_indexers"] = True
        config_payload["trigger_indexer_sync"] = True
        config_payload["prowlarr_auto_indexer_exclude_name_tokens"] = list(
            self.cfg.exclude_name_tokens
        )
        config_payload["prowlarr_indexer_reputation"] = dict(self.cfg.reputation_cfg)
        # Disable features that require PVCs/services not available to the auto-indexer pod.
        config_payload["arr_apps"] = []
        config_payload["app_auth"] = {"enabled": False, "include": []}
        for client in (config_payload.get("download_clients") or {}).values():
            if isinstance(client, dict):
                client["configure_arr_clients"] = False
                client["login_required"] = False
        for section_key in ("jellyseerr", "homepage", "bazarr", "maintainerr"):
            section = config_payload.get(section_key)
            if isinstance(section, dict):
                section["enabled"] = False
                section.setdefault("configure", False)
        with TemporaryDirectory(prefix="media-stack-controller-auto-config-") as tmpdir:
            config_json = Path(tmpdir) / "config.json"
            config_json.write_text(json.dumps(config_payload, indent=2) + "\n", encoding="utf-8")

            cm_yaml = Path(tmpdir) / "bootstrap-config-auto.yaml"
            generated = self.kube.run(
                [
                    "-n",
                    self.cfg.namespace,
                    "create",
                    "configmap",
                    "media-stack-controller-config-auto",
                    f"--from-file=config.json={config_json}",
                    "--dry-run=client",
                    "-o",
                    "yaml",
                ]
            )
            cm_yaml.write_text(generated.stdout, encoding="utf-8")
            self._replace_or_create_from_yaml(
                cm_yaml,
                "configmap/media-stack-controller-config-auto",
            )

    def recreate_job(self) -> None:
        """Trigger auto-indexers via the bootstrap service HTTP API.

        The bootstrap service handles auto-indexer discovery as an action
        (POST /actions/auto-indexers). This replaces the old pattern of
        creating a separate K8s Job from a manifest.
        """
        info("Triggering auto-indexers via bootstrap service API")
        pod_name = self._find_bootstrap_pod()
        if not pod_name:
            raise KubernetesError(
                "Bootstrap service pod not found — ensure media-stack-controller "
                "Deployment is running"
            )
        trigger_script = (
            "import urllib.request; "
            "req=urllib.request.Request("
            "'http://127.0.0.1:9100/actions/auto-indexers',"
            "data=b'{}',"
            "headers={'Content-Type':'application/json'}); "
            "r=urllib.request.urlopen(req,timeout=10); "
            "print(r.read().decode())"
        )
        result = self.kube.run(
            ["-n", self.cfg.namespace, "exec", pod_name, "--",
             "python3", "-c", trigger_script],
            check=False,
        )
        if result.stdout:
            info(f"Auto-indexer trigger response: {result.stdout.strip()}")
        if result.returncode != 0:
            raise KubernetesError(
                f"Auto-indexer trigger failed: {result.stderr or result.stdout}"
            )

    def _find_bootstrap_pod(self) -> str | None:
        result = self.kube.run(
            ["-n", self.cfg.namespace, "get", "pods",
             "-l", "app=media-stack-controller",
             "-o", "jsonpath={.items[0].metadata.name}"],
            check=False,
        )
        name = (result.stdout or "").strip()
        return name if name and result.returncode == 0 else None

    def _get_job(self, job_name: str) -> dict[str, Any] | None:
        result = self.kube.run(
            ["-n", self.cfg.namespace, "get", "job", job_name, "-o", "json"],
            check=False,
        )
        if result.returncode != 0:
            return None
        try:
            return json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return None

    def _get_pods(self, selector: str) -> dict[str, Any] | list[dict[str, Any]]:
        result = self.kube.run(
            ["-n", self.cfg.namespace, "get", "pods", "-l", selector, "-o", "json"],
            check=False,
        )
        if result.returncode != 0:
            return {"items": []}
        try:
            return json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return {"items": []}

    @staticmethod
    def _pod_items(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict):
            items = payload.get("items")
            return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    def _describe_tail(self, kind: str, name: str) -> str:
        result = self.kube.run(
            ["-n", self.cfg.namespace, "describe", kind, name],
            check=False,
        )
        return result.stdout or ""

    def _print_status_heartbeat(self, job_name: str, selector: str, elapsed: int) -> None:
        info(f"Waiting on job/{job_name} (elapsed {elapsed}s, timeout {self.cfg.timeout_raw})")

        job_table = self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "get",
                "job",
                job_name,
                "-o",
                "custom-columns=NAME:.metadata.name,COMPLETIONS:.status.succeeded,FAILED:.status.failed,ACTIVE:.status.active,AGE:.metadata.creationTimestamp",
                "--no-headers",
            ],
            check=False,
        )
        if job_table.stdout.strip():
            logger.info(job_table.stdout.rstrip())

        pod_table = self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "get",
                "pods",
                "-l",
                selector,
                "-o",
                "custom-columns=NAME:.metadata.name,PHASE:.status.phase,READY:.status.containerStatuses[0].ready,RESTARTS:.status.containerStatuses[0].restartCount",
                "--no-headers",
            ],
            check=False,
        )
        if pod_table.stdout.strip():
            logger.info(pod_table.stdout.rstrip())

    def _pending_scheduling_message(self, pod: dict[str, Any]) -> str:
        for condition in pod.get("status", {}).get("conditions", []) or []:
            if condition.get("type") == "PodScheduled":
                return str(condition.get("message") or "")
        return ""

    def _pods_for_job(self, job_name: str, job: dict[str, Any] | None) -> list[dict[str, Any]]:
        pods = self._pod_items(self._get_pods(f"job-name={job_name}"))
        job_uid = str(((job or {}).get("metadata") or {}).get("uid") or "")
        if not job_uid:
            return pods
        filtered = [
            item
            for item in pods
            if str(
                ((item.get("metadata") or {}).get("labels") or {}).get(
                    "batch.kubernetes.io/controller-uid"
                )
                or ((item.get("metadata") or {}).get("labels") or {}).get("controller-uid")
                or ""
            )
            == job_uid
        ]
        return filtered or pods

    def _print_pending_events(self, pod_name: str) -> None:
        describe = self._describe_tail("pod", pod_name)
        if not describe:
            return
        lines = describe.splitlines()
        if "Events:" in lines:
            idx = lines.index("Events:")
            events = lines[idx : idx + 16]
            logger.info("[PENDING] Events:")
            for line in events[1:]:
                logger.info("[PENDING] %s", line)

    def wait_for_job(self) -> None:
        job_name = "media-stack-prowlarr-auto-indexers"
        selector = "app=media-stack-prowlarr-auto-indexers"
        start = int(time.time())
        last_heartbeat = -self.cfg.heartbeat_interval
        last_pending_dump = -99999

        while True:
            now = int(time.time())
            elapsed = now - start

            if elapsed - last_heartbeat >= self.cfg.heartbeat_interval:
                self._print_status_heartbeat(job_name, selector, elapsed)
                last_heartbeat = elapsed

            job = self._get_job(job_name)
            if not job:
                warn(f"Job {self.cfg.namespace}/{job_name} not found while waiting.")
                self._print_failure_context(job_name, selector)
                raise KubernetesError("Auto-indexer job disappeared while waiting")

            status = job.get("status", {}) or {}
            succeeded = int(status.get("succeeded") or 0)
            failed = int(status.get("failed") or 0)
            conditions = status.get("conditions") or []

            complete = any(
                c.get("type") == "Complete" and c.get("status") == "True" for c in conditions
            )
            failed_condition = any(
                c.get("type") == "Failed" and c.get("status") == "True" for c in conditions
            )
            backoff = any(
                c.get("reason") == "BackoffLimitExceeded" and c.get("status") == "True"
                for c in conditions
            )

            if complete or succeeded >= 1:
                return
            if failed_condition or backoff or failed >= 1:
                warn("Job failed before completion.")
                self._print_failure_context(job_name, selector)
                raise KubernetesError("Auto-indexer job failed")

            pods = self._pods_for_job(job_name, job)
            pod = pods[0] if pods else None
            pod_name = str((pod or {}).get("metadata", {}).get("name") or "")
            pod_phase = str((pod or {}).get("status", {}).get("phase") or "")
            statuses = ((pod or {}).get("status", {}) or {}).get("containerStatuses") or []
            waiting = ((statuses[0].get("state") or {}).get("waiting") or {}) if statuses else {}
            wait_reason = str(waiting.get("reason") or "")
            wait_message = str(waiting.get("message") or "")

            if pod_phase in {"Failed", "Unknown"}:
                self._print_failure_context(job_name, selector)
                raise KubernetesError(f"Auto-indexer pod entered terminal phase: {pod_phase}")

            if wait_reason in {"ErrImagePull", "ImagePullBackOff"}:
                warn(f"Auto-indexer pod cannot pull bootstrap runner image ({wait_reason}).")
                if wait_message:
                    warn(f"Image pull message: {wait_message}")
                warn(
                    "Build/push the runner image and retry: bash bin/build-controller-image.sh"
                )
                self._print_failure_context(job_name, selector)
                raise KubernetesError("Auto-indexer pod failed due to bootstrap image pull error")

            if pod_phase == "Pending" and pod_name:
                if elapsed - last_pending_dump >= 45:
                    warn(f"Auto-indexer pod is Pending: {pod_name} (reason=unknown)")
                    self._print_pending_events(pod_name)
                    last_pending_dump = elapsed

                sched_message = self._pending_scheduling_message(pod)
                if elapsed >= 20 and re.search(
                    r"persistentvolumeclaim.*not\s+found", sched_message
                ):
                    warn("Auto-indexer pod remained Pending because required PVCs are missing.")
                    warn(f"Scheduling message: {sched_message}")
                    self._print_failure_context(job_name, selector)
                    raise KubernetesError("Auto-indexer pod pending due to missing PVC")

                hard_error = re.search(
                    r"persistentvolumeclaim|unbound immediate PersistentVolumeClaims|"
                    r"volume node affinity conflict|Multi-Attach|didn't match Pod's node affinity",
                    sched_message,
                )
                if elapsed >= 120 and hard_error:
                    warn(
                        "Auto-indexer pod remained Pending with a hard scheduling/storage error "
                        f"for {elapsed}s."
                    )
                    warn(f"Scheduling message: {sched_message}")
                    self._print_failure_context(job_name, selector)
                    raise KubernetesError("Auto-indexer pod pending with hard scheduling error")

            failed_pods = [
                item
                for item in self._pods_for_job(job_name, job)
                if str((item or {}).get("status", {}).get("phase") or "") == "Failed"
            ]
            if failed_pods:
                self._print_failure_context(job_name, selector)
                raise KubernetesError("Auto-indexer job has failed pods")

            if elapsed >= self.cfg.timeout_seconds:
                warn(f"Job did not complete successfully within {self.cfg.timeout_raw}")
                self._print_failure_context(job_name, selector)
                raise KubernetesError("Auto-indexer job timed out")

            time.sleep(2)

    def _print_failure_context(self, job_name: str, selector: str) -> None:
        describe_job = self.kube.run(
            ["-n", self.cfg.namespace, "describe", "job", job_name],
            check=False,
        )
        if describe_job.stdout.strip():
            logger.info(describe_job.stdout.rstrip())

        pods_wide = self.kube.run(
            ["-n", self.cfg.namespace, "get", "pods", "-l", selector, "-o", "wide"],
            check=False,
        )
        if pods_wide.stdout.strip():
            logger.info(pods_wide.stdout.rstrip())

        pods = self._pod_items(self._get_pods(selector))
        if pods:
            pod_name = str(pods[0].get("metadata", {}).get("name") or "")
            if pod_name:
                describe_pod = self.kube.run(
                    ["-n", self.cfg.namespace, "describe", "pod", pod_name],
                    check=False,
                )
                if describe_pod.stdout.strip():
                    logger.info(describe_pod.stdout.rstrip())

        logs = self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "logs",
                f"job/{job_name}",
                "--tail=300",
                "--timestamps",
            ],
            check=False,
        )
        if logs.stdout.strip():
            logger.info(logs.stdout.rstrip())

    def print_job_logs(self) -> None:
        logs = self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "logs",
                "job/media-stack-prowlarr-auto-indexers",
                "--tail=300",
                "--timestamps",
            ],
            check=False,
        )
        if logs.stdout.strip():
            logger.info(logs.stdout.rstrip())
