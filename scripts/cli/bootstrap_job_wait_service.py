"""Bootstrap Kubernetes Job wait/diagnostic helpers."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable

from core.exceptions import KubernetesError
from core.platforms.kubernetes.kube_client import KubernetesClient

LogFn = Callable[[str], None]
NowFn = Callable[[], int]
SleepFn = Callable[[float], None]


@dataclass(frozen=True)
class BootstrapJobWaitConfig:
    namespace: str
    timeout_seconds: int
    timeout_raw: str
    heartbeat_interval: int
    job_discovery_grace_seconds: int = 30
    job_missing_timeout_seconds: int = 60


@dataclass
class BootstrapJobWaitService:
    cfg: BootstrapJobWaitConfig
    kube: KubernetesClient
    info: LogFn
    warn: LogFn
    now: NowFn = lambda: int(time.time())
    sleep: SleepFn = time.sleep

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

    def _get_pods(self, selector: str) -> list[dict[str, Any]]:
        result = self.kube.run(
            ["-n", self.cfg.namespace, "get", "pods", "-l", selector, "-o", "json"],
            check=False,
        )
        if result.returncode != 0:
            return []
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return []
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            items = payload.get("items")
            return list(items) if isinstance(items, list) else []
        return []

    def _describe(self, kind: str, name: str) -> str:
        result = self.kube.run(
            ["-n", self.cfg.namespace, "describe", kind, name],
            check=False,
        )
        return result.stdout or ""

    def _heartbeat(self, job_name: str, selector: str, elapsed: int) -> None:
        self.info(f"Waiting on job/{job_name} (elapsed {elapsed}s, timeout {self.cfg.timeout_raw})")
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

    def _pod_schedule_reason_and_message(self, pod: dict[str, Any]) -> tuple[str, str]:
        for condition in pod.get("status", {}).get("conditions", []) or []:
            if condition.get("type") == "PodScheduled":
                return str(condition.get("reason") or ""), str(condition.get("message") or "")
        return "", ""

    def _print_pending_events(self, pod_name: str) -> None:
        describe = self._describe("pod", pod_name)
        if not describe:
            return
        lines = describe.splitlines()
        if "Events:" not in lines:
            return
        idx = lines.index("Events:")
        print("[PENDING] Events:")
        for line in lines[idx + 1 : idx + 16]:
            print(f"[PENDING] {line}")

    def _tail_pod_logs(self, pod_name: str, lines: int = 8) -> None:
        logs = self.kube.run(
            ["-n", self.cfg.namespace, "logs", pod_name, f"--tail={lines}"],
            check=False,
        )
        if logs.stdout.strip():
            for line in logs.stdout.rstrip().splitlines():
                print(f"[JOB] {line}")

    def _print_failure_context(self, job_name: str, selector: str) -> None:
        describe_job = self.kube.run(
            ["-n", self.cfg.namespace, "describe", "job", job_name],
            check=False,
        )
        if describe_job.stdout.strip():
            print(describe_job.stdout.rstrip())
        if describe_job.stderr.strip():
            print(describe_job.stderr.rstrip(), file=sys.stderr)

        pods_wide = self.kube.run(
            ["-n", self.cfg.namespace, "get", "pods", "-l", selector, "-o", "wide"],
            check=False,
        )
        if pods_wide.stdout.strip():
            print(pods_wide.stdout.rstrip())
        if pods_wide.stderr.strip():
            print(pods_wide.stderr.rstrip(), file=sys.stderr)

        pods = self._get_pods(selector)
        if pods:
            pod_name = str(pods[0].get("metadata", {}).get("name") or "")
            if pod_name:
                describe_pod = self.kube.run(
                    ["-n", self.cfg.namespace, "describe", "pod", pod_name],
                    check=False,
                )
                if describe_pod.stdout.strip():
                    print(describe_pod.stdout.rstrip())
                if describe_pod.stderr.strip():
                    print(describe_pod.stderr.rstrip(), file=sys.stderr)

        job_logs = self.kube.run(
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
        if job_logs.stdout.strip():
            print(job_logs.stdout.rstrip())
        if job_logs.stderr.strip():
            print(job_logs.stderr.rstrip(), file=sys.stderr)

    def wait_for_job(self, *, job_name: str, selector: str) -> None:
        start = self.now()
        last_heartbeat = -self.cfg.heartbeat_interval
        last_pending_dump = -99999
        job_last_seen_elapsed: int | None = None

        while True:
            elapsed = self.now() - start

            if elapsed - last_heartbeat >= self.cfg.heartbeat_interval:
                self._heartbeat(job_name, selector, elapsed)
                last_heartbeat = elapsed

            job = self._get_job(job_name)
            if not job:
                if elapsed < self.cfg.job_discovery_grace_seconds:
                    self.sleep(1)
                    continue
                if job_last_seen_elapsed is None:
                    missing_for = elapsed - self.cfg.job_discovery_grace_seconds
                else:
                    missing_for = elapsed - job_last_seen_elapsed
                if missing_for < self.cfg.job_missing_timeout_seconds:
                    self.sleep(1)
                    continue
                self.warn(f"Job {self.cfg.namespace}/{job_name} not found while waiting.")
                raise KubernetesError("Bootstrap job disappeared while waiting")
            job_last_seen_elapsed = elapsed

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
                self.warn("Job failed before completion.")
                self._print_failure_context(job_name, selector)
                raise KubernetesError("Bootstrap job failed")

            pods = self._get_pods(f"job-name={job_name}")
            job_uid = str((job.get("metadata") or {}).get("uid") or "")
            if job_uid:
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
                if filtered:
                    pods = filtered
            pods.sort(
                key=lambda item: str((item.get("metadata") or {}).get("creation_timestamp") or ""),
                reverse=True,
            )
            pod = pods[0] if pods else None
            if pod:
                pod_name = str(pod.get("metadata", {}).get("name") or "")
                pod_phase = str(pod.get("status", {}).get("phase") or "")
                statuses = pod.get("status", {}).get("containerStatuses") or []
                wait_reason = ""
                wait_message = ""
                if statuses and isinstance(statuses, list):
                    waiting = (statuses[0] or {}).get("state", {}).get("waiting", {}) or {}
                    wait_reason = str(waiting.get("reason") or "")
                    wait_message = str(waiting.get("message") or "")

                if wait_reason in ("ErrImagePull", "ImagePullBackOff"):
                    self.warn(f"Job pod cannot pull bootstrap runner image ({wait_reason}).")
                    if wait_message:
                        self.warn(f"Image pull message: {wait_message}")
                    self.warn(
                        "Build/push the runner image and retry: "
                        "bash scripts/build-bootstrap-runner-image.sh"
                    )
                    raise KubernetesError("Bootstrap job image pull failed")

                if pod_phase in ("Failed", "Unknown"):
                    self.warn("Job failed before completion.")
                    self._print_failure_context(job_name, selector)
                    raise KubernetesError("Bootstrap job pod failed")

                if pod_phase == "Pending":
                    reason, sched_message = self._pod_schedule_reason_and_message(pod)
                    if elapsed - last_pending_dump >= 45:
                        self.warn(f"Job pod is Pending: {pod_name} (reason={reason or 'unknown'})")
                        if sched_message:
                            self.warn(f"Job pod scheduling message: {sched_message}")
                        if pod_name:
                            self._print_pending_events(pod_name)
                        last_pending_dump = elapsed
                    if (
                        elapsed >= 20
                        and "persistentvolumeclaim" in sched_message
                        and "not found" in sched_message
                    ):
                        self.warn("Job pod remained Pending because required PVCs are missing.")
                        self.warn(f"Scheduling message: {sched_message}")
                        raise KubernetesError("Missing required PVCs for bootstrap job")
                    if elapsed >= 120 and any(
                        marker in sched_message
                        for marker in (
                            "persistentvolumeclaim",
                            "unbound immediate PersistentVolumeClaims",
                            "volume node affinity conflict",
                            "Multi-Attach",
                            "didn't match Pod's node affinity",
                        )
                    ):
                        self.warn(
                            "Job pod remained Pending with a hard scheduling/storage "
                            f"error for {elapsed}s."
                        )
                        self.warn(f"Scheduling message: {sched_message}")
                        raise KubernetesError("Bootstrap job scheduling failed")
                elif pod_name:
                    self._tail_pod_logs(pod_name, lines=8)

            if elapsed >= self.cfg.timeout_seconds:
                self.warn(f"Job did not complete within {self.cfg.timeout_raw}.")
                self._print_failure_context(job_name, selector)
                raise KubernetesError("Bootstrap job timed out")

            self.sleep(2)
