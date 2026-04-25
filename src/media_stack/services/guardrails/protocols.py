"""Guardrail protocol — the small interface every rule must satisfy.

A guardrail is a self-contained policy statement: "don't let condition
X happen". It looks at a snapshot of system state and returns a
``Severity`` if the condition is breached, else ``None``.

Why a Protocol and not a base class? Each domain (``storage``,
``bandwidth``, ``auth`` …) has very different state requirements;
inheritance would either bloat a base class with ``Optional`` fields
or force every concrete rule to call ``super().__init__()``. Duck
typing via ``Protocol`` keeps each rule readable in isolation while
letting the registry treat them uniformly.

The state dict passed to ``evaluate`` and ``remediate`` is the
``SystemState`` snapshot the ``state_collector`` builds each tick.
Rules MUST NOT do any I/O — all data they need is in ``state``. This
keeps tick latency bounded and makes evaluation testable with a hand-
rolled state dict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Protocol, runtime_checkable


# Fixed severity ladder. ``ok`` is implied by ``evaluate`` returning
# ``None``; we don't have an explicit ``ok`` Severity so the type
# system catches "I forgot to return None" bugs at the call site.
Severity = Literal["info", "warning", "critical"]
Domain = Literal[
    "storage",
    "bandwidth",
    "external_api",
    "media_quality",
    "job_health",
    "auth",
    "dependency",
    "cost",
]


@dataclass
class Trigger:
    """One guardrail evaluation that fired. Ordered by severity then
    rule id so consumers can build deterministic UI."""

    rule_id: str
    domain: str
    severity: Severity
    description: str
    current_value: Any = None
    threshold: Any = None
    detail: str = ""
    evaluated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "domain": self.domain,
            "severity": self.severity,
            "description": self.description,
            "current_value": self.current_value,
            "threshold": self.threshold,
            "detail": self.detail,
            "evaluated_at": self.evaluated_at,
        }


@dataclass
class Action:
    """Result of a remediation attempt. ``action`` is a free-form
    short token (e.g. ``"throttle"``, ``"qbit_cleanup"``,
    ``"notify"``) the UI / audit log can render. ``ok`` is False when
    the rule decided not to act (e.g. throttle is already in effect)
    — distinct from a raised exception, which the registry catches
    and records as ``ok=False, detail=str(exc)``."""

    rule_id: str
    action: str
    ok: bool
    detail: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "action": self.action,
            "ok": self.ok,
            "detail": self.detail,
            "extra": dict(self.extra),
        }


@runtime_checkable
class Guardrail(Protocol):
    """The minimal interface every rule must implement.

    ``evaluate`` returns a ``Severity`` if the rule fired, else
    ``None``. The registry layers history-tracking, threshold
    overrides, and disable-toggle on top.

    ``remediate`` is optional in practice — a rule that has no auto-
    action (e.g. cost caps that only notify) returns ``None`` from
    ``remediate`` and lets the alert engine handle the human side.
    """

    id: str
    domain: str
    description: str
    default_threshold: Mapping[str, Any]

    def evaluate(self, state: Mapping[str, Any]) -> Severity | None: ...

    def remediate(self, state: Mapping[str, Any]) -> Action | None: ...


__all__ = [
    "Action",
    "Domain",
    "Guardrail",
    "Severity",
    "Trigger",
]
