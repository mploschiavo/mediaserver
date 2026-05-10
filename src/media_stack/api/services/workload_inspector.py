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
    # Unix timestamp when the CURRENT container started (i.e. since
    # the last restart). ``None`` when unknown / not running. The
    # crashloop classifier uses this to distinguish "currently
    # crashlooping" (just restarted) from "had restarts earlier
    # today but stable now" — the latter shouldn't fire critical
    # health stories.
    running_since: float | None = None


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
        # Current-container start time. ``cs.state.running.started_at``
        # is a ``datetime`` from the k8s client; convert to unix epoch
        # so the classifier can compare against ``time.time()``.
        running_since: float | None = None
        state = getattr(cs, "state", None)
        running_state = getattr(state, "running", None) if state else None
        started_at = getattr(running_state, "started_at", None) if running_state else None
        if started_at is not None:
            try:
                running_since = float(started_at.timestamp())
            except (AttributeError, ValueError, TypeError):
                running_since = None
        return WorkloadState(
            service_id=sid,
            running=bool(pod.status and pod.status.phase == "Running"),
            restart_count=int(getattr(cs, "restart_count", 0) or 0),
            last_terminated_reason=reason,
            last_terminated_exit_code=exit_code,
            running_since=running_since,
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
    """Compose-aware container introspector.

    Service IDs in our SERVICES registry (``controller``, ``ui``,
    ``grabit``, ``jdownloader``...) do NOT always match docker
    container names. Compose lets each service set ``container_name``
    independently, and many of our services use prefixed names
    (``media-stack-controller``, ``media-stack-ui``). Other service
    IDs in the registry refer to optional services that aren't even
    deployed in the active compose profile (``authentik`` is K8s-only,
    ``grabit``/``jdownloader`` ride opt-in profiles).

    Looking up by raw service_id therefore floods the controller log
    with ``404 Client Error ... No such container`` for every probe
    cycle. Fix: maintain a service_id → container mapping using the
    canonical ``com.docker.compose.service`` label that compose stamps
    on every container it manages, and fall back to the raw name only
    when no labelled match is found. Service IDs with no live
    container on either path return a clean ``not running`` state
    (no exception, no log spam).
    """

    def __init__(self, client) -> None:
        self._client = client

    def list_workloads(
        self, service_ids: Iterable[str],
    ) -> dict[str, WorkloadState]:
        ids = list(service_ids)
        # Build the lookup once per probe cycle so a 30-service stack
        # doesn't pay for 30 docker.list() round-trips.
        labelled = self._build_labelled_lookup()
        out: dict[str, WorkloadState] = {}
        for sid in ids:
            out[sid] = self._inspect(sid, labelled)
        return out

    def _build_labelled_lookup(self) -> dict[str, object]:
        """Return ``{compose_service_name: Container}`` for every
        container the local docker daemon currently manages. Uses the
        canonical compose label so the mapping survives renames."""
        try:
            containers = list(self._client.containers.list(all=True))
        except Exception as exc:  # noqa: BLE001
            _log.debug(
                "[DEBUG] DockerWorkloadInspector: list_all failed: %s", exc,
            )
            return {}
        out: dict[str, object] = {}
        for container in containers:
            try:
                labels = (
                    getattr(container, "labels", None)
                    or (getattr(container, "attrs", {}) or {})
                    .get("Config", {}).get("Labels", {})
                    or {}
                )
            except Exception:  # noqa: BLE001
                labels = {}
            svc = str(labels.get("com.docker.compose.service") or "").strip()
            if svc and svc not in out:
                out[svc] = container
        return out

    def _resolve_container(self, sid: str, labelled: dict[str, object]):
        """Map a service-id to a live container, trying:

        1. The labelled lookup (compose-managed containers).
        2. The compose ``media-stack-`` prefix (``controller`` →
           ``media-stack-controller``).
        3. The raw service_id as a literal container name.

        Returns ``None`` if none match — the inspector treats that as
        a not-running service rather than crashing.
        """
        cand = labelled.get(sid)
        if cand is not None:
            return cand
        prefixed = labelled.get(f"media-stack-{sid}")
        if prefixed is not None:
            return prefixed
        try:
            return self._client.containers.get(sid)
        except Exception:  # noqa: BLE001
            try:
                return self._client.containers.get(f"media-stack-{sid}")
            except Exception:  # noqa: BLE001
                return None

    def _inspect(
        self, sid: str, labelled: dict[str, object] | None = None,
    ) -> WorkloadState:
        labelled = labelled if labelled is not None \
            else self._build_labelled_lookup()
        container = self._resolve_container(sid, labelled)
        if container is None:
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
            # Parse the StartedAt ISO timestamp into a unix epoch
            # so the crashloop classifier can age the current
            # container. ``StartedAt`` from docker-py is an ISO
            # string like ``2026-05-10T20:59:07.123456789Z``.
            running_since: float | None = None
            started_at_raw = state.get("StartedAt") or ""
            if started_at_raw and started_at_raw != "0001-01-01T00:00:00Z":
                import datetime
                try:
                    ts = started_at_raw.replace("Z", "+00:00")
                    # docker-py emits 9-digit nanoseconds; datetime
                    # only takes microseconds → trim if needed.
                    if "." in ts:
                        head, frac_tz = ts.split(".", 1)
                        if "+" in frac_tz:
                            frac, tz = frac_tz.split("+", 1)
                            ts = f"{head}.{frac[:6]}+{tz}"
                        elif "-" in frac_tz:
                            frac, tz = frac_tz.split("-", 1)
                            ts = f"{head}.{frac[:6]}-{tz}"
                    running_since = datetime.datetime.fromisoformat(ts).timestamp()
                except (ValueError, TypeError):
                    running_since = None
            return WorkloadState(
                service_id=sid,
                running=running,
                restart_count=restart_count,
                last_terminated_reason=str(reason or ""),
                last_terminated_exit_code=exit_code,
                running_since=running_since,
            )
        except Exception as exc:
            _log.debug("[DEBUG] inspect %s: state read failed: %s",
                       sid, exc)
            return WorkloadState(sid, False, 0, "", -1)

    def previous_logs(
        self, service_id: str, *, tail_lines: int = 200,
    ) -> str:
        labelled = self._build_labelled_lookup()
        container = self._resolve_container(service_id, labelled)
        if container is None:
            return ""
        try:
            data = container.logs(tail=tail_lines, stdout=True, stderr=True)
            return data.decode("utf-8", errors="replace") \
                if isinstance(data, bytes) else str(data or "")
        except Exception as exc:
            _log.debug("[DEBUG] previous_logs %s: %s", service_id, exc)
            return ""


# ----------------------------------------------------------------------
# Auto-detect factory
# ----------------------------------------------------------------------


class WorkloadInspectorFactory:
    """Probes the environment to construct a platform-appropriate
    ``WorkloadInspector``.

    Stateless; instantiated once at module import as ``_INSTANCE`` and
    exposed through the ``build_default_inspector`` module-level alias
    so callers (``api.services.crashloop``, ``api.services.auto_heal``)
    keep their function-style import surface. The kubernetes/docker
    SDK imports stay deferred to preserve the lazy-load behaviour
    (kubernetes pulls a wide HTTP/cert dep set we don't want at
    module-load time on compose-only deployments).
    """

    def build(self) -> WorkloadInspector:
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


_INSTANCE = WorkloadInspectorFactory()
build_default_inspector = _INSTANCE.build
