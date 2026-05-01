"""Promise types — ADR-0003 Phase 4a.

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
    "PromiseRegistryError",
    "ProbeSpec",
]
