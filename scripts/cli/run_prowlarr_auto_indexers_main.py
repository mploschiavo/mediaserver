#!/usr/bin/env python3
"""Run Prowlarr auto-indexer discovery job with Kubernetes orchestration."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from core.exceptions import ConfigError, KubernetesError, MediaStackError
from core.kube import KubectlClient


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
            info(
                f"  {phase_name} => {self.phase_results[idx]} "
                f"({self.phase_seconds[idx]}s)"
            )


class ProwlarrAutoIndexerRunner:
    def __init__(
        self,
        cfg: AutoIndexerConfig,
        kube: KubectlClient,
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
            "192.168.1.60:30002/library/media-stack-bootstrap-runner:latest",
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

    def update_auto_configmap(self) -> None:
        info("Updating temporary bootstrap config ConfigMap")
        config_payload = {
            "prowlarr_url": "http://prowlarr:9696",
            "trigger_indexer_sync": True,
            "arr_apps": [],
            "prowlarr_auto_indexer_exclude_name_tokens": list(self.cfg.exclude_name_tokens),
            "prowlarr_indexer_reputation": dict(self.cfg.reputation_cfg),
        }
        with TemporaryDirectory(prefix="media-stack-bootstrap-auto-config-") as tmpdir:
            config_json = Path(tmpdir) / "config.json"
            config_json.write_text(json.dumps(config_payload, indent=2) + "\n", encoding="utf-8")

            cm_yaml = Path(tmpdir) / "bootstrap-config-auto.yaml"
            generated = self.kube.run(
                [
                    "-n",
                    self.cfg.namespace,
                    "create",
                    "configmap",
                    "media-stack-bootstrap-config-auto",
                    f"--from-file=config.json={config_json}",
                    "--dry-run=client",
                    "-o",
                    "yaml",
                ]
            )
            cm_yaml.write_text(generated.stdout, encoding="utf-8")
            self._replace_or_create_from_yaml(
                cm_yaml,
                "configmap/media-stack-bootstrap-config-auto",
            )

    def recreate_job(self) -> None:
        self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "delete",
                "job",
                "media-stack-prowlarr-auto-indexers",
                "--ignore-not-found",
            ],
            check=False,
        )
        info("Creating auto-indexer Job")

        manifest_path = self.cfg.root_dir / "k8s" / "prowlarr-auto-indexers-job.yaml"
        with TemporaryDirectory(prefix="media-stack-auto-indexer-job-") as tmpdir:
            patched = Path(tmpdir) / "prowlarr-auto-indexers-job.yaml"
            patched.write_text(
                self.manifest_overrides(manifest_path.read_text(encoding="utf-8")),
                encoding="utf-8",
            )
            result = self.kube.run(["apply", "-f", str(patched)], check=False)
            if result.stdout.strip():
                print(result.stdout.rstrip())
            if result.stderr.strip():
                print(result.stderr.rstrip(), file=sys.stderr)
            if result.returncode != 0:
                raise KubernetesError(result.stderr or result.stdout)

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

    def _get_pods(self, selector: str) -> dict[str, Any]:
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
            print(job_table.stdout.rstrip())

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
            print(pod_table.stdout.rstrip())

    def _pending_scheduling_message(self, pod: dict[str, Any]) -> str:
        for condition in pod.get("status", {}).get("conditions", []) or []:
            if condition.get("type") == "PodScheduled":
                return str(condition.get("message") or "")
        return ""

    def _print_pending_events(self, pod_name: str) -> None:
        describe = self._describe_tail("pod", pod_name)
        if not describe:
            return
        lines = describe.splitlines()
        if "Events:" in lines:
            idx = lines.index("Events:")
            events = lines[idx : idx + 16]
            print("[PENDING] Events:")
            for line in events[1:]:
                print(f"[PENDING] {line}")

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

            complete = any(c.get("type") == "Complete" and c.get("status") == "True" for c in conditions)
            failed_condition = any(c.get("type") == "Failed" and c.get("status") == "True" for c in conditions)
            backoff = any(c.get("reason") == "BackoffLimitExceeded" and c.get("status") == "True" for c in conditions)

            if complete or succeeded >= 1:
                return
            if failed_condition or backoff or failed >= 1:
                warn("Job failed before completion.")
                self._print_failure_context(job_name, selector)
                raise KubernetesError("Auto-indexer job failed")

            pods = self._get_pods(f"job-name={job_name}").get("items") or []
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
                warn("Build/push the runner image and retry: bash scripts/build-bootstrap-runner-image.sh")
                self._print_failure_context(job_name, selector)
                raise KubernetesError("Auto-indexer pod failed due to bootstrap image pull error")

            if pod_phase == "Pending" and pod_name:
                if elapsed - last_pending_dump >= 45:
                    warn(f"Auto-indexer pod is Pending: {pod_name} (reason=unknown)")
                    self._print_pending_events(pod_name)
                    last_pending_dump = elapsed

                sched_message = self._pending_scheduling_message(pod)
                if elapsed >= 20 and re.search(r"persistentvolumeclaim.*not\s+found", sched_message):
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
                for item in (self._get_pods(f"job-name={job_name}").get("items") or [])
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
            print(describe_job.stdout.rstrip())

        pods_wide = self.kube.run(
            ["-n", self.cfg.namespace, "get", "pods", "-l", selector, "-o", "wide"],
            check=False,
        )
        if pods_wide.stdout.strip():
            print(pods_wide.stdout.rstrip())

        pods = self._get_pods(selector).get("items") or []
        if pods:
            pod_name = str(pods[0].get("metadata", {}).get("name") or "")
            if pod_name:
                describe_pod = self.kube.run(
                    ["-n", self.cfg.namespace, "describe", "pod", pod_name],
                    check=False,
                )
                if describe_pod.stdout.strip():
                    print(describe_pod.stdout.rstrip())

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
            print(logs.stdout.rstrip())

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
            print(logs.stdout.rstrip())


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts/run-prowlarr-auto-indexers.sh",
        description=(
            "Auto-discovers Prowlarr indexer templates/presets, tests each, and adds only "
            "those that pass."
        ),
    )
    parser.add_argument("--namespace", default="media-stack")
    parser.add_argument("--timeout", default="20m")
    parser.add_argument("--heartbeat-interval", type=int, default=15)
    parser.add_argument("--prepare-host-root", default="/srv/media-stack")
    parser.add_argument(
        "--bootstrap-runner-image",
        default=os.getenv(
            "BOOTSTRAP_RUNNER_IMAGE",
            "192.168.1.60:30002/library/media-stack-bootstrap-runner:latest",
        ),
    )
    parser.add_argument(
        "--exclude-name-token",
        action="append",
        default=None,
        help=(
            "Exclude auto-indexer candidates whose name contains this token. "
            "Can be passed multiple times."
        ),
    )
    parser.add_argument(
        "--reputation-state-path",
        default=os.getenv(
            "AUTO_INDEXER_REPUTATION_STATE_PATH",
            "/srv-config/prowlarr/indexer-reputation-state.json",
        ),
        help="State file path for indexer reputation scoring and quarantine.",
    )
    parser.add_argument(
        "--quarantine-score-threshold",
        type=int,
        default=int(os.getenv("AUTO_INDEXER_QUARANTINE_SCORE_THRESHOLD", "-10")),
        help="Quarantine an indexer when score <= threshold.",
    )
    parser.add_argument(
        "--quarantine-failure-threshold",
        type=int,
        default=int(os.getenv("AUTO_INDEXER_QUARANTINE_FAILURE_THRESHOLD", "3")),
        help="Quarantine an indexer when failure count >= threshold.",
    )
    parser.add_argument(
        "--quarantine-ttl-hours",
        type=int,
        default=int(os.getenv("AUTO_INDEXER_QUARANTINE_TTL_HOURS", "72")),
        help="Auto-unquarantine after this TTL (hours).",
    )
    return parser


def parse_config(argv: list[str] | None = None) -> AutoIndexerConfig:
    args = build_arg_parser().parse_args(argv)
    namespace = str(args.namespace or "").strip()
    timeout_raw = str(args.timeout or "").strip()
    heartbeat_interval = int(args.heartbeat_interval)
    prepare_host_root = str(args.prepare_host_root or "").strip()
    bootstrap_runner_image = str(args.bootstrap_runner_image or "").strip()
    cli_excludes = [str(item).strip().lower() for item in (args.exclude_name_token or []) if str(item).strip()]
    env_excludes = [
        item.strip().lower()
        for item in str(os.getenv("AUTO_INDEXER_EXCLUDE_NAME_TOKENS", "")).split(",")
        if item.strip()
    ]
    default_excludes = [
        "the pirate bay",
        "limetorrents",
        "torrentgalaxyclone",
    ]
    exclude_name_tokens = list(dict.fromkeys(cli_excludes + env_excludes + default_excludes))

    if not namespace:
        raise ConfigError("namespace must be non-empty")
    if not timeout_raw:
        raise ConfigError("timeout must be non-empty")
    if heartbeat_interval <= 0:
        raise ConfigError("heartbeat interval must be > 0")
    if not prepare_host_root:
        raise ConfigError("prepare host root must be non-empty")
    if not bootstrap_runner_image:
        raise ConfigError("bootstrap runner image must be non-empty")

    return AutoIndexerConfig(
        namespace=namespace,
        timeout_raw=timeout_raw,
        heartbeat_interval=heartbeat_interval,
        prepare_host_root=prepare_host_root,
        bootstrap_runner_image=bootstrap_runner_image,
        exclude_name_tokens=exclude_name_tokens,
        reputation_cfg={
            "enabled": True,
            "state_path": str(args.reputation_state_path or "").strip(),
            "quarantine_score_threshold": int(args.quarantine_score_threshold),
            "quarantine_failure_threshold": int(args.quarantine_failure_threshold),
            "quarantine_ttl_hours": int(args.quarantine_ttl_hours),
        },
        root_dir=Path(__file__).resolve().parents[2],
    )


def main(argv: list[str] | None = None) -> int:
    tracker = PhaseTracker()
    try:
        cfg = parse_config(argv)
        runner = ProwlarrAutoIndexerRunner(
            cfg=cfg,
            kube=KubectlClient.from_environment(),
            tracker=tracker,
        )
        return runner.run()
    except (ConfigError, KubernetesError, MediaStackError) as exc:
        if tracker.current_phase:
            tracker.end("failed")
        warn(f"Auto-indexer job runner failed: {exc}")
        tracker.print_summary()
        return 1


if __name__ == "__main__":
    sys.exit(main())
