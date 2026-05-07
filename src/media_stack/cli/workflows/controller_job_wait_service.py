"""Bootstrap Kubernetes Deployment/Job wait/diagnostic helpers."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from media_stack.core.exceptions import KubernetesError
from media_stack.core.platforms.kubernetes.kube_client import KubernetesClient

LogFn = Callable[[str], None]
NowFn = Callable[[], int]
SleepFn = Callable[[float], None]

logger = logging.getLogger("bootstrap_wait")


@dataclass(frozen=True)
class ControllerJobWaitConfig:
    namespace: str
    timeout_seconds: int
    timeout_raw: str
    heartbeat_interval: int
    job_discovery_grace_seconds: int = 30
    job_missing_timeout_seconds: int = 60
    service_name: str = "bootstrap"
    service_port: int = 9100


@dataclass
class BootstrapPodHttpClient:
    """``kubectl exec``-driven HTTP probes against the controller pod.

    Two endpoints are interesting to the CLI wait flow:

    - ``GET /status`` — deployment-state flags
      (``initial_bootstrap_done``, ``error``, ``action_history``).
    - ``GET /api/jobs/running`` — Job framework's live tree, used
      to detect a still-in-flight action by ``job_name``.

    Extracted from ``ControllerJobWaitService`` so the wait service
    keeps a coherent (sub-500-line) shape.
    """

    kube: KubernetesClient
    namespace: str
    service_port: int

    def _exec_http(self, pod_name: str, path: str) -> str | None:
        result = self.kube.run(
            [
                "-n", self.namespace, "exec", pod_name, "--",
                "python3", "-c",
                "import urllib.request,json; "
                f"r=urllib.request.urlopen('http://127.0.0.1:{self.service_port}{path}'); "
                "print(r.read().decode())",
            ],
            check=False,
        )
        if result.returncode != 0:
            return None
        return result.stdout or ""

    def query_status(self, pod_name: str) -> dict[str, Any] | None:
        body = self._exec_http(pod_name, "/status")
        if body is None:
            return None
        try:
            return json.loads(body or "{}")
        except json.JSONDecodeError:
            return None

    def query_running_action_names(self, pod_name: str) -> set[str]:
        body = self._exec_http(pod_name, "/api/jobs/running")
        if body is None:
            return set()
        try:
            payload = json.loads(body or "{}")
        except json.JSONDecodeError:
            return set()

        running: set[str] = set()

        def walk(node: dict[str, Any]) -> None:
            name = node.get("job_name")
            if isinstance(name, str) and name:
                running.add(name)
            for child in node.get("children") or []:
                if isinstance(child, dict):
                    walk(child)

        for top in payload.get("tree") or []:
            if isinstance(top, dict):
                walk(top)
        return running


@dataclass
class ControllerJobWaitService:
    cfg: ControllerJobWaitConfig
    kube: KubernetesClient
    info: LogFn
    warn: LogFn
    now: NowFn = lambda: int(time.time())
    sleep: SleepFn = time.sleep
    success_markers: tuple[str, ...] = (
        "[OK] Bootstrap completed successfully",
        "[OK] Bootstrap complete.",
    )

    def __post_init__(self) -> None:
        self._pod_http = BootstrapPodHttpClient(
            kube=self.kube,
            namespace=self.cfg.namespace,
            service_port=self.cfg.service_port,
        )

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
        logger.info("[PENDING] Events:")
        for line in lines[idx + 1 : idx + 16]:
            logger.info("[PENDING] %s", line)

    def _tail_pod_logs(self, pod_name: str, lines: int = 8) -> None:
        logs = self.kube.run(
            ["-n", self.cfg.namespace, "logs", pod_name, f"--tail={lines}"],
            check=False,
        )
        if logs.stdout.strip():
            for line in logs.stdout.rstrip().splitlines():
                logger.info("[JOB] %s", line)

    def _logs_contain_success_marker(self, job_name: str) -> bool:
        logs = self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "logs",
                f"job/{job_name}",
                "--tail=300",
            ],
            check=False,
        )
        if logs.returncode != 0:
            return False
        output = str(logs.stdout or "")
        return any(marker in output for marker in self.success_markers)

    def _print_failure_context(self, job_name: str, selector: str) -> None:
        describe_job = self.kube.run(
            ["-n", self.cfg.namespace, "describe", "job", job_name],
            check=False,
        )
        if describe_job.stdout.strip():
            logger.info(describe_job.stdout.rstrip())
        if describe_job.stderr.strip():
            logger.error(describe_job.stderr.rstrip())

        pods_wide = self.kube.run(
            ["-n", self.cfg.namespace, "get", "pods", "-l", selector, "-o", "wide"],
            check=False,
        )
        if pods_wide.stdout.strip():
            logger.info(pods_wide.stdout.rstrip())
        if pods_wide.stderr.strip():
            logger.error(pods_wide.stderr.rstrip())

        pods = self._get_pods(selector)
        if pods:
            pod_name = str(pods[0].get("metadata", {}).get("name") or "")
            if pod_name:
                describe_pod = self.kube.run(
                    ["-n", self.cfg.namespace, "describe", "pod", pod_name],
                    check=False,
                )
                if describe_pod.stdout.strip():
                    logger.info(describe_pod.stdout.rstrip())
                if describe_pod.stderr.strip():
                    logger.error(describe_pod.stderr.rstrip())

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
            logger.info(job_logs.stdout.rstrip())
        if job_logs.stderr.strip():
            logger.error(job_logs.stderr.rstrip())

    def wait_for_job(self, *, job_name: str, selector: str) -> None:
        start = self.now()
        last_heartbeat = -self.cfg.heartbeat_interval
        last_pending_dump = -99999
        last_success_probe = -self.cfg.heartbeat_interval
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

            if elapsed - last_success_probe >= self.cfg.heartbeat_interval:
                if self._logs_contain_success_marker(job_name):
                    self.info(f"Detected bootstrap success marker in logs for job/{job_name}.")
                    return
                last_success_probe = elapsed

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
                        "bash bin/build-controller-image.sh"
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

    # ------------------------------------------------------------------
    # HTTP-based wait for persistent Deployment bootstrap service
    # ------------------------------------------------------------------

    def _find_bootstrap_pod(self, selector: str = "app=media-stack-controller") -> str | None:
        """Return the name of the first ready bootstrap pod, or None."""
        pods = self._get_pods(selector)
        for pod in pods:
            pod_name = str(pod.get("metadata", {}).get("name") or "")
            phase = str(pod.get("status", {}).get("phase") or "")
            if phase == "Running" and pod_name:
                return pod_name
        return None

    def _query_bootstrap_status(self, pod_name: str) -> dict[str, Any] | None:
        return self._pod_http.query_status(pod_name)

    def _query_running_action_names(self, pod_name: str) -> set[str]:
        return self._pod_http.query_running_action_names(pod_name)

    def wait_for_bootstrap_service(
        self,
        *,
        selector: str = "app=media-stack-controller",
        wait_for_action: str | None = None,
    ) -> None:
        """Wait for the bootstrap Deployment's HTTP service to report completion.

        Polls GET /status until:
        - ``initial_bootstrap_done`` is True and ``error`` is None
          (initial bootstrap)
        - ``wait_for_action`` (when specified) no longer appears in
          ``GET /api/jobs/running`` — i.e. the action has finished.

        ADR-0005 Phase 5a: terminal-state and current-action checks
        no longer consume the legacy ``phase`` / ``current_action``
        fields. ``initial_bootstrap_done`` + ``error`` + the Job
        framework's running-tree are the canonical signals.
        """
        start = self.now()
        last_heartbeat = -self.cfg.heartbeat_interval
        pod_name: str | None = None

        self.info(
            f"Waiting for bootstrap service "
            f"(namespace={self.cfg.namespace}, timeout={self.cfg.timeout_raw})"
        )

        while True:
            elapsed = self.now() - start

            # Heartbeat.
            if elapsed - last_heartbeat >= self.cfg.heartbeat_interval:
                self.info(
                    f"Bootstrap service poll: elapsed {elapsed}s, "
                    f"timeout {self.cfg.timeout_raw}, "
                    f"pod={'found' if pod_name else 'waiting'}"
                )
                last_heartbeat = elapsed

            # Find the bootstrap pod.
            if not pod_name:
                pod_name = self._find_bootstrap_pod(selector)
                if not pod_name:
                    if elapsed >= self.cfg.timeout_seconds:
                        raise KubernetesError(
                            "Bootstrap service pod not found within timeout"
                        )
                    self.sleep(3)
                    continue

            # Query /status.
            status = self._query_bootstrap_status(pod_name)
            if status is None:
                # Pod might be starting up.
                if elapsed >= self.cfg.timeout_seconds:
                    raise KubernetesError(
                        "Bootstrap service did not respond within timeout"
                    )
                self.sleep(3)
                continue

            error = status.get("error")
            initial_done = bool(status.get("initial_bootstrap_done"))

            # Check if we're waiting for a specific action to finish.
            if wait_for_action:
                running_actions = self._query_running_action_names(pod_name)
                logger.debug(
                    "Status: initial_done=%s, error=%s, running=%s",
                    initial_done, error, sorted(running_actions),
                )
                # Action is running — keep waiting.
                if wait_for_action in running_actions:
                    if elapsed >= self.cfg.timeout_seconds:
                        raise KubernetesError(
                            f"Action '{wait_for_action}' did not complete within timeout"
                        )
                    self.sleep(3)
                    continue
                # Action is no longer running — check history for result.
                history = status.get("action_history") or []
                for record in reversed(history):
                    if record.get("name") == wait_for_action:
                        if record.get("error"):
                            raise KubernetesError(
                                f"Action '{wait_for_action}' failed: {record['error']}"
                            )
                        self.info(
                            f"Action '{wait_for_action}' completed successfully "
                            f"({record.get('elapsed_seconds', '?')}s)"
                        )
                        return
                # Action not in history yet and not running — it may not have started.
                if elapsed >= self.cfg.timeout_seconds:
                    raise KubernetesError(
                        f"Action '{wait_for_action}' not found in service status"
                    )
                self.sleep(3)
                continue

            # Default: wait for initial bootstrap to complete.
            logger.debug(
                "Status: initial_done=%s, error=%s",
                initial_done, error,
            )
            if initial_done and error is None:
                self.info(
                    f"Bootstrap service reports complete "
                    f"(elapsed={status.get('elapsed_seconds', '?')}s)"
                )
                return

            if error:
                self.warn(f"Bootstrap service reports error: {error}")
                # Print pod logs for diagnostics.
                if pod_name:
                    self._tail_pod_logs(pod_name, lines=30)
                raise KubernetesError(f"Bootstrap service failed: {error}")

            if elapsed >= self.cfg.timeout_seconds:
                self.warn(
                    f"Bootstrap service did not complete within {self.cfg.timeout_raw} "
                    f"(initial_done={initial_done})"
                )
                if pod_name:
                    self._tail_pod_logs(pod_name, lines=30)
                raise KubernetesError("Bootstrap service timed out")

            self.sleep(3)
