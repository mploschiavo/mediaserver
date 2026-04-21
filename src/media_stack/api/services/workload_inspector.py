"""Thin platform-agnostic inspector for service workloads.

Higher-level features (crashloop classifier, auto-heal job) need
two pieces of platform-specific information about each service:

1. **Restart state** — how many times has the workload restarted,
   what was the last termination reason (CrashLoopBackOff, OOMKilled,
   plain Error, …).
2. **Previous-instance logs** — the *last completed* container's
   stderr/stdout. On K8s this is ``kubectl logs --previous``; on
   Docker it's the last log batch before a restart.

Without this split the higher-level code would have to import
``kubernetes`` and ``docker`` directly and branch on platform —
which spreads SDK details across modules. Here we keep the
branching in one place.

The inspector intentionally never raises: every failure mode
(missing SDK, no in-cluster config, no matching pod) is folded
into a "no info available" result so a single broken probe can't
take the dashboard down."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Iterable, Protocol

_log = logging.getLogger("controller_api")


@dataclass(frozen=True)
class WorkloadState:
    """One service's runtime state, normalised across platforms."""

    service_id: str
    running: bool
    restart_count: int
    last_terminated_reason: str   # "OOMKilled", "Error", "Completed", ""
    last_terminated_exit_code: int  # -1 if unknown


class WorkloadInspector(Protocol):
    """Protocol every backend implements."""

    def list_workloads(
        self, service_ids: Iterable[str],
    ) -> dict[str, WorkloadState]: ...

    def previous_logs(
        self, service_id: str, *, tail_lines: int = 200,
    ) -> str: ...


# ----------------------------------------------------------------------
# Null backend — used when neither K8s nor Docker SDKs initialise
# ----------------------------------------------------------------------


class NullWorkloadInspector:
    """Returns 'no information available' for everything. Safe
    fallback so the controller still boots in dev environments
    that don't have an SDK."""

    def list_workloads(
        self, service_ids: Iterable[str],
    ) -> dict[str, WorkloadState]:
        return {
            sid: WorkloadState(
                service_id=sid,
                running=False,
                restart_count=0,
                last_terminated_reason="",
                last_terminated_exit_code=-1,
            )
            for sid in service_ids
        }

    def previous_logs(
        self, service_id: str, *, tail_lines: int = 200,
    ) -> str:
        return ""


# ----------------------------------------------------------------------
# Kubernetes backend
# ----------------------------------------------------------------------


class KubernetesWorkloadInspector:

    def __init__(self, namespace: str, core_v1) -> None:
        self._namespace = namespace
        self._core_v1 = core_v1

    def list_workloads(
        self, service_ids: Iterable[str],
    ) -> dict[str, WorkloadState]:
        out: dict[str, WorkloadState] = {}
        for sid in service_ids:
            out[sid] = self._inspect(sid)
        return out

    def _inspect(self, sid: str) -> WorkloadState:
        try:
            resp = self._core_v1.list_namespaced_pod(
                namespace=self._namespace,
                label_selector=f"app={sid}",
            )
        except Exception as exc:
            _log.debug("[DEBUG] inspect %s: list pods failed: %s", sid, exc)
            return WorkloadState(sid, False, 0, "", -1)

        pods = list(getattr(resp, "items", []) or [])
        if not pods:
            return WorkloadState(sid, False, 0, "", -1)

        # Prefer the running pod if multiple exist; otherwise pick
        # whichever the K8s API returned first (usually most recent).
        pods.sort(
            key=lambda p: 0 if (p.status and p.status.phase == "Running") else 1,
        )
        pod = pods[0]
        statuses = (pod.status.container_statuses if pod.status else None) or []
        if not statuses:
            return WorkloadState(
                sid,
                bool(pod.status and pod.status.phase == "Running"),
                0, "", -1,
            )
        cs = statuses[0]
        last = getattr(cs, "last_state", None)
        terminated = getattr(last, "terminated", None) if last else None
        reason = getattr(terminated, "reason", "") or ""
        exit_code = int(getattr(terminated, "exit_code", -1) or -1)
        return WorkloadState(
            service_id=sid,
            running=bool(pod.status and pod.status.phase == "Running"),
            restart_count=int(getattr(cs, "restart_count", 0) or 0),
            last_terminated_reason=reason,
            last_terminated_exit_code=exit_code,
        )

    def previous_logs(
        self, service_id: str, *, tail_lines: int = 200,
    ) -> str:
        try:
            resp = self._core_v1.list_namespaced_pod(
                namespace=self._namespace,
                label_selector=f"app={service_id}",
            )
        except Exception as exc:
            _log.debug("[DEBUG] previous_logs list pods %s: %s",
                       service_id, exc)
            return ""
        pods = list(getattr(resp, "items", []) or [])
        if not pods:
            return ""
        pod_name = pods[0].metadata.name
        try:
            return str(self._core_v1.read_namespaced_pod_log(
                name=pod_name,
                namespace=self._namespace,
                previous=True,
                tail_lines=tail_lines,
            ) or "")
        except Exception as exc:
            _log.debug("[DEBUG] previous_logs read %s: %s",
                       service_id, exc)
            return ""


# ----------------------------------------------------------------------
# Docker / compose backend
# ----------------------------------------------------------------------


class DockerWorkloadInspector:

    def __init__(self, client) -> None:
        self._client = client

    def list_workloads(
        self, service_ids: Iterable[str],
    ) -> dict[str, WorkloadState]:
        out: dict[str, WorkloadState] = {}
        for sid in service_ids:
            out[sid] = self._inspect(sid)
        return out

    def _inspect(self, sid: str) -> WorkloadState:
        try:
            container = self._client.containers.get(sid)
        except Exception as exc:
            _log.debug("[DEBUG] inspect %s: get container failed: %s",
                       sid, exc)
            return WorkloadState(sid, False, 0, "", -1)
        try:
            container.reload()
            attrs = getattr(container, "attrs", {}) or {}
            state = attrs.get("State", {}) or {}
            running = bool(state.get("Running"))
            restart_count = int(attrs.get("RestartCount", 0) or 0)
            exit_code = int(state.get("ExitCode", -1) or -1)
            reason = "OOMKilled" if state.get("OOMKilled") else (
                state.get("Error") or
                ("Error" if exit_code not in (-1, 0) else "")
            )
            return WorkloadState(
                service_id=sid,
                running=running,
                restart_count=restart_count,
                last_terminated_reason=str(reason or ""),
                last_terminated_exit_code=exit_code,
            )
        except Exception as exc:
            _log.debug("[DEBUG] inspect %s: state read failed: %s",
                       sid, exc)
            return WorkloadState(sid, False, 0, "", -1)

    def previous_logs(
        self, service_id: str, *, tail_lines: int = 200,
    ) -> str:
        try:
            container = self._client.containers.get(service_id)
            data = container.logs(tail=tail_lines, stdout=True, stderr=True)
            return data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data or "")
        except Exception as exc:
            _log.debug("[DEBUG] previous_logs %s: %s", service_id, exc)
            return ""


# ----------------------------------------------------------------------
# Auto-detect factory
# ----------------------------------------------------------------------


def build_default_inspector() -> WorkloadInspector:
    """Probe the environment in this order:

    1. ``K8S_NAMESPACE`` set + ``kubernetes`` SDK importable +
       in-cluster config loadable → K8s backend.
    2. ``docker`` SDK importable + Docker socket accessible →
       Docker backend.
    3. Otherwise → ``NullWorkloadInspector``.

    The detection is one-shot at construction; rebuild the
    instance if the platform changes underneath."""
    if os.environ.get("K8S_NAMESPACE"):
        try:
            from kubernetes import client, config  # type: ignore
            try:
                config.load_incluster_config()
            except Exception:
                config.load_kube_config()
            return KubernetesWorkloadInspector(
                namespace=os.environ["K8S_NAMESPACE"],
                core_v1=client.CoreV1Api(),
            )
        except Exception as exc:
            _log.debug("[DEBUG] K8s inspector init failed: %s", exc)
    try:
        import docker  # type: ignore
        return DockerWorkloadInspector(docker.from_env())
    except Exception as exc:
        _log.debug("[DEBUG] Docker inspector init failed: %s", exc)
    return NullWorkloadInspector()
