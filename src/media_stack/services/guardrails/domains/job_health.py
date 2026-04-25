"""Job-health guardrails.

State expected:

- ``state["job_history"]`` — output of
  ``cli.commands.job_framework.get_job_history()``.
- ``state["auto_heal"]["cycles_per_hour"]`` — count from the last
  hour as recorded by the auto-heal service.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..protocols import Action, Severity
from ..registry import register_guardrail


_DOMAIN = "job_health"


@dataclass
class _RuntimeCap:
    id: str = "job:runtime_cap"
    domain: str = _DOMAIN
    description: str = (
        "Largest single-job elapsed time in the recent history. A "
        "long tail means an upstream service is timing out repeatedly."
    )
    default_threshold: Mapping[str, Any] = field(
        default_factory=lambda: {"max_seconds": 300}
    )

    def evaluate(self, state: Mapping[str, Any]) -> Severity | None:
        threshold = state.get("_threshold:" + self.id) or self.default_threshold
        cap = float(threshold.get("max_seconds", 300) or 0)
        if cap <= 0:
            return None
        history = state.get("job_history") or []
        if not isinstance(history, list):
            return None
        worst = 0.0
        for batch in history[:5] if len(history) >= 5 else history:
            if not isinstance(batch, dict):
                continue
            for body in (batch.get("jobs") or {}).values():
                if not isinstance(body, dict):
                    continue
                try:
                    elapsed = float(body.get("elapsed") or 0)
                except (TypeError, ValueError):
                    continue
                if elapsed > worst:
                    worst = elapsed
        if worst > cap:
            return "critical" if worst > cap * 2 else "warning"
        return None

    def remediate(self, state: Mapping[str, Any]) -> Action | None:
        return Action(rule_id=self.id, action="notify", ok=False,
                      detail="long-running job detected; check upstream timeouts")


@dataclass
class _ConsecutiveErrors:
    id: str = "job:consecutive_errors"
    domain: str = _DOMAIN
    description: str = (
        "A specific job has erred for the last N runs in a row. "
        "Distinct from job-flapping — this fires earlier and harder."
    )
    default_threshold: Mapping[str, Any] = field(
        default_factory=lambda: {"max_consecutive": 3}
    )

    def evaluate(self, state: Mapping[str, Any]) -> Severity | None:
        threshold = state.get("_threshold:" + self.id) or self.default_threshold
        cap = int(threshold.get("max_consecutive", 3) or 0)
        if cap <= 0:
            return None
        history = state.get("job_history") or []
        if not isinstance(history, list):
            return None
        # Per-job consecutive errors counted from the newest end.
        consecutive: dict[str, int] = {}
        broken: set[str] = set()
        for batch in history:
            if not isinstance(batch, dict):
                continue
            jobs = batch.get("jobs") or {}
            for name, body in jobs.items():
                if name in broken:
                    continue
                if not isinstance(body, dict):
                    continue
                if str(body.get("status") or "").lower() == "error":
                    consecutive[name] = consecutive.get(name, 0) + 1
                else:
                    broken.add(name)
        if any(n >= cap for n in consecutive.values()):
            return "critical"
        return None

    def remediate(self, state: Mapping[str, Any]) -> Action | None:
        return Action(rule_id=self.id, action="notify", ok=False,
                      detail="job has erred N times in a row")


@dataclass
class _AutoHealCycleCap:
    id: str = "job:auto_heal_cycle_cap"
    domain: str = _DOMAIN
    description: str = (
        "Auto-heal cycles per hour. A high rate signals a service is "
        "stuck in a heal/break loop."
    )
    default_threshold: Mapping[str, Any] = field(
        default_factory=lambda: {"max_cycles_per_hour": 10}
    )

    def evaluate(self, state: Mapping[str, Any]) -> Severity | None:
        threshold = state.get("_threshold:" + self.id) or self.default_threshold
        cap = int(threshold.get("max_cycles_per_hour", 10) or 0)
        if cap <= 0:
            return None
        n = int((state.get("auto_heal") or {}).get("cycles_per_hour") or 0)
        if n >= cap:
            return "critical" if n >= cap * 2 else "warning"
        return None

    def remediate(self, state: Mapping[str, Any]) -> Action | None:
        return Action(rule_id=self.id, action="notify", ok=False,
                      detail="auto-heal looping; investigate root cause")


register_guardrail(_RuntimeCap())
register_guardrail(_ConsecutiveErrors())
register_guardrail(_AutoHealCycleCap())
