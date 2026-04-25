"""Dependency-provider guardrails.

State expected:

- ``state["dependency"]["provider_down_minutes"]`` — dict
  ``provider_id → minutes_unreachable``. Populated by the state
  collector from the existing health probe history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..protocols import Action, Severity
from ..registry import register_guardrail


_DOMAIN = "dependency"


@dataclass
class _ProviderUnreachable:
    id: str = "dep:provider_unreachable"
    domain: str = _DOMAIN
    description: str = (
        "Authelia, Jellyfin or Jellyseerr unreachable for longer than "
        "the threshold. The longer the outage the higher the severity."
    )
    default_threshold: Mapping[str, Any] = field(
        default_factory=lambda: {"max_minutes": 5}
    )

    def evaluate(self, state: Mapping[str, Any]) -> Severity | None:
        threshold = state.get("_threshold:" + self.id) or self.default_threshold
        max_min = float(threshold.get("max_minutes", 5) or 0)
        if max_min <= 0:
            return None
        downtimes = (state.get("dependency") or {}).get(
            "provider_down_minutes"
        ) or {}
        if not isinstance(downtimes, dict):
            return None
        worst = 0.0
        for value in downtimes.values():
            try:
                m = float(value)
            except (TypeError, ValueError):
                continue
            if m > worst:
                worst = m
        if worst > max_min:
            return "critical" if worst > max_min * 3 else "warning"
        return None

    def remediate(self, state: Mapping[str, Any]) -> Action | None:
        return Action(rule_id=self.id, action="notify", ok=False,
                      detail="provider down; failover or alert operator")

    def current_value(self, state: Mapping[str, Any]) -> dict[str, float]:
        downtimes = (state.get("dependency") or {}).get(
            "provider_down_minutes"
        ) or {}
        if not isinstance(downtimes, dict):
            return {}
        out: dict[str, float] = {}
        for k, v in downtimes.items():
            try:
                out[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
        return out


register_guardrail(_ProviderUnreachable())
