"""Promise types (see ADR-0003).

A ``Promise`` is one declarative entry in
``contracts/promises/promises.yaml``: "after a fresh install, X is
true". Every promise has

  * an ``id`` (operator-friendly identifier)
  * a ``probe`` — how to verify the invariant holds
  * an ``ensurer`` — how to make it true if the probe fails
  * optional ``depends_on`` — other promises that must hold first

Both ``probe`` and ``ensurer`` are discriminated unions so the
orchestrator dispatcher (Phase 4b) can pattern-match on the type
without per-handler if-statements.

Pure domain types — no I/O, no logging, no framework imports. The
loader (``infrastructure/promises/registry.py``) parses YAML into
these; the orchestrator (``application/services/orchestrator.py``)
dispatches against them.

Two YAML shapes coexist by design:

  * Existing entries use ``ensured_by: <string>`` referring to a
    JobRunner job by name (legacy schema, ~50 entries today).
  * New entries use ``ensured_by: { type: lifecycle, ... }`` referring
    to a ``ServiceLifecycle`` method.

The loader maps both into typed values; downstream code doesn't care
which schema produced the entry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Union


# ============================================================================
# ProbeSpec — discriminated union over how to verify a promise
# ============================================================================


@dataclass(frozen=True)
class LifecycleProbe:
    """Probe via a ``ServiceLifecycle`` method (probe_running,
    probe_has_api_key). The orchestrator looks up the lifecycle class
    from the named service's contract YAML."""

    kind: Literal["lifecycle"] = "lifecycle"
    service: str = ""
    method: str = ""


@dataclass(frozen=True)
class HttpJsonProbe:
    """Probe by GET'ing a JSON endpoint and asserting against the
    parsed body. Used by most existing cross-service promises."""

    kind: Literal["http_json"] = "http_json"
    service: str = ""
    path: str = ""
    auth: str = "none"  # api_key | none
    assert_expr: str = ""


@dataclass(frozen=True)
class HttpTextProbe:
    """Probe by GET'ing a text endpoint and asserting against the body."""

    kind: Literal["http_text"] = "http_text"
    service: str = ""
    path: str = ""
    auth: str = "none"
    assert_expr: str = ""


@dataclass(frozen=True)
class HttpStatusProbe:
    """Probe by GET'ing an endpoint and asserting against the status code."""

    kind: Literal["http_status"] = "http_status"
    service: str = ""
    path: str = ""
    auth: str = "none"
    assert_expr: str = ""


@dataclass(frozen=True)
class FileJsonProbe:
    """Probe by reading a JSON file and asserting against the parsed body."""

    kind: Literal["file_json"] = "file_json"
    path: str = ""
    assert_expr: str = ""
    skip_if_missing: bool = False


@dataclass(frozen=True)
class FileTextProbe:
    """Probe by reading a text file and asserting against the contents."""

    kind: Literal["file_text"] = "file_text"
    path: str = ""
    assert_expr: str = ""
    skip_if_missing: bool = False


@dataclass(frozen=True)
class K8sResourceProbe:
    """K8s-only probe via ``kubectl get`` against a typed resource."""

    kind: Literal["k8s_resource"] = "k8s_resource"
    resource_kind: str = ""
    namespace: str = ""
    label_selector: str = ""
    assert_expr: str = ""


@dataclass(frozen=True)
class K8sExecProbe:
    """K8s-only probe via ``kubectl exec`` into a pod."""

    kind: Literal["k8s_exec"] = "k8s_exec"
    namespace: str = ""
    pod_label: str = ""
    container: str = ""
    command: tuple[str, ...] = ()
    assert_expr: str = ""
    skip_if_unset: str = ""


ProbeSpec = Union[
    LifecycleProbe,
    HttpJsonProbe,
    HttpTextProbe,
    HttpStatusProbe,
    FileJsonProbe,
    FileTextProbe,
    K8sResourceProbe,
    K8sExecProbe,
]


# ============================================================================
# EnsurerSpec — discriminated union over how to make a promise true
# ============================================================================


@dataclass(frozen=True)
class LifecycleEnsurer:
    """Ensure via a ``ServiceLifecycle`` method (mint_api_key, etc.)."""

    kind: Literal["lifecycle"] = "lifecycle"
    service: str = ""
    method: str = ""


@dataclass(frozen=True)
class JobEnsurer:
    """Ensure by running a registered JobRunner job by name. The
    legacy schema's ``ensured_by: ensure-foo-job`` strings parse to
    this. Most existing promises today land here."""

    kind: Literal["job"] = "job"
    job_name: str = ""


@dataclass(frozen=True)
class DeployEnsurer:
    """Ensure by triggering a compose/k8s deploy for a target. The
    ADR-0003 sketch uses ``ensured_by: { type: deploy, target: jellyfin }``
    for service-running promises. The orchestrator delegates the
    actual deploy to platform-specific runners; out of scope for
    Phase 4 (probably Phase 5+)."""

    kind: Literal["deploy"] = "deploy"
    target: str = ""


@dataclass(frozen=True)
class InfraEnsurer:
    """Ensure by an out-of-band operator/platform action.
    ``kubectl-apply``, ``operator``, ``seed-runtime-overrides`` —
    things the orchestrator can't run itself; the operator (or a
    cluster-level controller) is responsible. The orchestrator
    records these as 'externally ensured' and only re-probes."""

    kind: Literal["infra"] = "infra"
    operator: str = ""


EnsurerSpec = Union[
    LifecycleEnsurer,
    JobEnsurer,
    DeployEnsurer,
    InfraEnsurer,
]


# ============================================================================
# Promise
# ============================================================================


@dataclass(frozen=True)
class Promise:
    """One declarative invariant the stack guarantees post-install.

    Frozen so the orchestrator can stash the loaded registry once
    and treat it as a constant for the process lifetime; YAML re-
    loads are intentional (operator edits the file, controller
    restarts).
    """

    id: str
    description: str
    platforms: tuple[str, ...]
    probe: ProbeSpec
    ensurer: EnsurerSpec
    depends_on: tuple[str, ...] = ()

    def applies_to(self, platform: str) -> bool:
        """Whether this promise applies on the given platform
        (``compose`` / ``k8s``). The orchestrator skips promises that
        don't apply on the current runtime."""
        return platform in self.platforms


# ============================================================================
# Errors
# ============================================================================


class PromiseRegistryError(ValueError):
    """Raised by the registry loader on malformed entries. Includes
    the offending promise id and a one-line reason so YAML errors
    are operator-actionable, not "unhelpful KeyError on line 723"."""


# ============================================================================
# Cooldown state — durable, per-promise backoff tracker (Phase 4b)
# ============================================================================


PromiseStatus = Literal[
    "ok",
    "failed_transient",
    "failed_permanent",
    "dep_failed",
    "skipped_cooldown",
    "skipped_platform",
    "unknown",
]
"""Per-promise outcome the orchestrator records each tick.

``ok``                — probe returned ok (or ensurer ran successfully and
                        re-probe returned ok).
``failed_transient``  — probe failed, ensurer returned ``transient=True``,
                        or ensurer ran but re-probe still failed
                        transiently. Eligible for retry next tick.
``failed_permanent``  — ensurer returned ``transient=False`` (config-level
                        problem), or repeated transient failures escalated.
                        Operator action expected; retried with longer
                        cooldown.
``dep_failed``        — a ``depends_on`` promise failed this tick; the
                        orchestrator skipped this one to avoid wasted work.
``skipped_cooldown``  — promise's last attempt was within the backoff
                        window. Will retry on a future tick.
``skipped_platform``  — platform mismatch (``platforms: [k8s]`` on a
                        compose runtime, etc.).
``unknown``           — orchestrator couldn't classify the result (e.g.
                        an unexpected exception bubbled up). Logged at
                        ERROR; treated as transient for cooldown
                        purposes.
"""


@dataclass(frozen=True)
class PromiseAttempt:
    """One ``satisfy_promises`` evaluation of one promise.

    Frozen, JSON-serializable. The cooldown tracker keeps the latest
    attempt per promise (``last_attempt: PromiseAttempt | None``) so
    the next tick can decide whether the backoff window has elapsed.
    """

    promise_id: str
    status: PromiseStatus
    started_at: float
    elapsed_seconds: float
    detail: str = ""
    probe_evidence: Mapping[str, Any] = field(default_factory=dict)
    ensurer_fired: bool = False
    ensurer_attempts: int = 0
    consecutive_failures: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "promise_id": self.promise_id,
            "status": self.status,
            "started_at": self.started_at,
            "elapsed_seconds": self.elapsed_seconds,
            "detail": self.detail,
            "probe_evidence": dict(self.probe_evidence),
            "ensurer_fired": self.ensurer_fired,
            "ensurer_attempts": self.ensurer_attempts,
            "consecutive_failures": self.consecutive_failures,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PromiseAttempt":
        return cls(
            promise_id=str(data.get("promise_id", "")),
            status=str(data.get("status", "unknown")),  # type: ignore[arg-type]
            started_at=float(data.get("started_at", 0.0)),
            elapsed_seconds=float(data.get("elapsed_seconds", 0.0)),
            detail=str(data.get("detail", "")),
            probe_evidence=dict(data.get("probe_evidence") or {}),
            ensurer_fired=bool(data.get("ensurer_fired", False)),
            ensurer_attempts=int(data.get("ensurer_attempts", 0)),
            consecutive_failures=int(data.get("consecutive_failures", 0)),
        )


# ============================================================================
# Tick summary — what one ``satisfy_promises`` call returned
# ============================================================================


@dataclass(frozen=True)
class TickSummary:
    """Aggregate result of one ``satisfy_promises(registry, ctx)`` call.

    The orchestrator returns this so callers (auto-heal hook, CLI,
    discrepancy logger) can render it without re-walking the
    per-promise attempts. The list of attempts is preserved on
    ``attempts`` for finer-grained inspection.
    """

    started_at: float
    elapsed_seconds: float
    total: int
    ok: int
    failed_transient: int
    failed_permanent: int
    dep_failed: int
    skipped_cooldown: int
    skipped_platform: int
    unknown: int
    attempts: tuple[PromiseAttempt, ...] = ()

    @property
    def has_failures(self) -> bool:
        return (
            self.failed_transient
            + self.failed_permanent
            + self.dep_failed
            + self.unknown
        ) > 0

    def summary_line(self) -> str:
        """Human-friendly one-line summary suitable for INFO logs."""
        parts = [f"{self.ok} ok"]
        if self.failed_transient:
            parts.append(f"{self.failed_transient} transient")
        if self.failed_permanent:
            parts.append(f"{self.failed_permanent} permanent")
        if self.dep_failed:
            parts.append(f"{self.dep_failed} dep_failed")
        if self.skipped_cooldown:
            parts.append(f"{self.skipped_cooldown} cooldown")
        if self.skipped_platform:
            parts.append(f"{self.skipped_platform} platform_skip")
        if self.unknown:
            parts.append(f"{self.unknown} unknown")
        return ", ".join(parts)


__all__ = [
    "DeployEnsurer",
    "EnsurerSpec",
    "FileJsonProbe",
    "FileTextProbe",
    "HttpJsonProbe",
    "HttpStatusProbe",
    "HttpTextProbe",
    "InfraEnsurer",
    "JobEnsurer",
    "K8sExecProbe",
    "K8sResourceProbe",
    "LifecycleEnsurer",
    "LifecycleProbe",
    "Promise",
    "PromiseAttempt",
    "PromiseRegistryError",
    "PromiseStatus",
    "ProbeSpec",
    "TickSummary",
]
