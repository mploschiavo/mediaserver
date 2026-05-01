"""Service-lifecycle Protocol + value types — ADR-0003 Phase 1.

A single Protocol every service implements, so the orchestrator can
ask uniform questions ("is this service running?", "does it have an
API key?", "mint one if missing") regardless of the underlying
technology. ADR-0003 Context section covers the motivation: 29
services today each answer those questions with bespoke code (5
``*HttpPreflight`` classes, 4 ``*ComposePreflight`` classes, 4 SQLite
readers for Jellyfin alone). The Protocol here lets the per-service
adapters collapse into one shape; Phase 2 lands ``JellyfinLifecycle``
and ``ServarrLifecycle`` as the proofs.

Why a Protocol and not a base class? Same rationale as
``domain/guardrails/protocols.py::Guardrail`` — every service has
genuinely different needs (Jellyfin reads SQLite, Servarr reads
config.xml, qBittorrent mints a session cookie). Inheritance would
either bloat a base with optional fields or force ``super().__init__``
boilerplate. ``runtime_checkable`` Protocol keeps each adapter
readable in isolation while the orchestrator and ratchet treat them
uniformly.

Pure domain types — no I/O, no logging, no framework imports. The
adapters in ``adapters/<service>/lifecycle.py`` do the I/O; this file
just declares the shape the orchestrator and the ratchet enforce.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Generic,
    Literal,
    Mapping,
    Protocol,
    TypeVar,
    runtime_checkable,
)


# --- ProbeResult -----------------------------------------------------

ProbeStatus = Literal["ok", "failed", "unknown"]
"""Tri-state probe outcome.

``ok``       — the invariant holds (service responding, key present, etc.).
``failed``   — the invariant is verifiably broken (service down, key
               missing). The orchestrator runs the matched ensurer.
``unknown``  — the probe couldn't tell. Network error mid-check, prereq
               not yet satisfied, transient timeout. Treated as
               ``failed`` for the run-the-ensurer decision but logged
               separately so operators can distinguish "we know it's
               broken" from "we couldn't ask". An always-``unknown``
               probe is a probe-design bug and should fail the ratchet.
"""


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of a single probe call.

    Frozen so the orchestrator can cache results across a tick without
    worrying about mutation. ``evidence`` carries structured fields the
    UI can render (latency, last status code, key count); ``detail`` is
    the one-line human-readable summary.
    """

    status: ProbeStatus
    detail: str = ""
    evidence: Mapping[str, Any] = field(default_factory=dict)
    evaluated_at: float = 0.0

    @classmethod
    def ok(
        cls,
        detail: str = "",
        *,
        evidence: Mapping[str, Any] | None = None,
        evaluated_at: float = 0.0,
    ) -> "ProbeResult":
        return cls(
            status="ok",
            detail=detail,
            evidence=dict(evidence or {}),
            evaluated_at=evaluated_at,
        )

    @classmethod
    def failed(
        cls,
        detail: str,
        *,
        evidence: Mapping[str, Any] | None = None,
        evaluated_at: float = 0.0,
    ) -> "ProbeResult":
        return cls(
            status="failed",
            detail=detail,
            evidence=dict(evidence or {}),
            evaluated_at=evaluated_at,
        )

    @classmethod
    def unknown(
        cls,
        detail: str,
        *,
        evidence: Mapping[str, Any] | None = None,
        evaluated_at: float = 0.0,
    ) -> "ProbeResult":
        return cls(
            status="unknown",
            detail=detail,
            evidence=dict(evidence or {}),
            evaluated_at=evaluated_at,
        )

    @property
    def is_ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "detail": self.detail,
            "evidence": dict(self.evidence),
            "evaluated_at": self.evaluated_at,
        }


# --- Outcome[T] ------------------------------------------------------

T_co = TypeVar("T_co", covariant=True)


@dataclass(frozen=True)
class Outcome(Generic[T_co]):
    """Result of an ensurer call (mint, persist, etc.).

    Two shapes:
      * ``Outcome.success(value)`` — the ensurer ran and the world is
        now in the desired state. ``value`` carries the produced
        artifact (e.g. the minted API key); ``Outcome[None]`` is fine
        for void operations like ``persist_api_key``.
      * ``Outcome.failure(error, transient=...)`` — the ensurer could
        not reach the desired state. ``transient=True`` signals that
        the orchestrator should retry on the next auto-heal tick (API
        was 503, target service warming up). ``transient=False`` means
        config-level failure (bad credentials, missing dependency) and
        should not be retried without operator action.

    Frozen for the same reason as ProbeResult.
    """

    ok: bool
    value: T_co | None = None
    error: str = ""
    transient: bool = False
    attempts: int = 1
    elapsed_seconds: float = 0.0
    evidence: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def success(
        cls,
        value: T_co | None = None,
        *,
        attempts: int = 1,
        elapsed_seconds: float = 0.0,
        evidence: Mapping[str, Any] | None = None,
    ) -> "Outcome[T_co]":
        return cls(
            ok=True,
            value=value,
            attempts=attempts,
            elapsed_seconds=elapsed_seconds,
            evidence=dict(evidence or {}),
        )

    @classmethod
    def failure(
        cls,
        error: str,
        *,
        transient: bool = False,
        attempts: int = 1,
        elapsed_seconds: float = 0.0,
        evidence: Mapping[str, Any] | None = None,
    ) -> "Outcome[T_co]":
        return cls(
            ok=False,
            value=None,
            error=error,
            transient=transient,
            attempts=attempts,
            elapsed_seconds=elapsed_seconds,
            evidence=dict(evidence or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "value": self.value,
            "error": self.error,
            "transient": self.transient,
            "attempts": self.attempts,
            "elapsed_seconds": self.elapsed_seconds,
            "evidence": dict(self.evidence),
        }


# --- OrchestrationContext --------------------------------------------

@dataclass(frozen=True)
class OrchestrationContext:
    """Read-only runtime passed to every probe and ensurer.

    The adapter receives everything it needs through this object — the
    contract YAML config (host, port, paths, auth modes), resolved
    secrets (env vars + file-mounted secrets, already merged), an
    injectable time source for testability, and a co-operative cancel
    signal for long-running ensurers. The adapter does NOT reach for
    globals; the orchestrator owns the wiring.

    Frozen so a probe can't mutate context the next probe sees.
    Mutable maps inside (``config``, ``secrets``, ``extra``) are passed
    as ``Mapping`` to discourage in-place writes; if an adapter genuinely
    needs to mutate state, it goes through a ``persist_*`` ensurer, not
    through context.
    """

    service_id: str
    config: Mapping[str, Any] = field(default_factory=dict)
    secrets: Mapping[str, str] = field(default_factory=dict)
    now: Callable[[], float] = field(default=lambda: 0.0, repr=False)
    is_cancelled: Callable[[], bool] = field(default=lambda: False, repr=False)
    dry_run: bool = False
    extra: Mapping[str, Any] = field(default_factory=dict)


# --- ServiceLifecycle Protocol ---------------------------------------

@runtime_checkable
class ServiceLifecycle(Protocol):
    """The minimal interface every service adapter must implement.

    Five methods cover the full lifecycle the orchestrator cares about:

      * ``probe_running``       — is the service answering?
      * ``probe_has_api_key``   — does the service have a usable
                                  credential the controller can wield?
      * ``mint_api_key``        — produce one if missing. MUST be
                                  idempotent — return the existing key
                                  if discoverable; never re-mint
                                  unnecessarily.
      * ``discover_api_key``    — single canonical READ path. Replaces
                                  the four bespoke SQLite/XML readers
                                  scattered across the codebase today.
      * ``persist_api_key``     — write to wherever the contract YAML
                                  says (env, secrets file, controller
                                  state). Idempotent.

    Services without an API-key concept (homepage, envoy) implement
    ``probe_has_api_key`` returning ``ProbeResult.ok("no api key
    concept")`` and the mint/discover/persist methods returning
    ``Outcome.success`` with ``None``. That's preferable to making the
    methods Optional — the ratchet stays simple, the orchestrator
    treats every service uniformly, and "this service has no key" is
    expressed in data, not in the type system.

    Class-level attribute ``service_id`` matches the contract YAML's
    ``service.id`` field. The orchestrator uses this to look up the
    config, the secrets, and the matching promise registry entries.
    """

    service_id: str

    def probe_running(self, ctx: OrchestrationContext) -> ProbeResult: ...

    def probe_has_api_key(self, ctx: OrchestrationContext) -> ProbeResult: ...

    def mint_api_key(self, ctx: OrchestrationContext) -> Outcome[str]: ...

    def discover_api_key(self, ctx: OrchestrationContext) -> str | None: ...

    def persist_api_key(
        self, key: str, ctx: OrchestrationContext,
    ) -> Outcome[None]: ...


__all__ = [
    "OrchestrationContext",
    "Outcome",
    "ProbeResult",
    "ProbeStatus",
    "ServiceLifecycle",
]
