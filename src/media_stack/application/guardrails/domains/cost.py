"""Cost-domain guardrails.

Cloud-only — for self-hosted bare-metal deployments these are
placeholders that always return ``None`` until the operator sets
``state["cost"]["egress_gb_month"]``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from media_stack.domain.guardrails.protocols import Action, Severity

from ..registry import register_guardrail


_DOMAIN = "cost"


@dataclass
class _EgressGbMonthCap:
    id: str = "cost:egress_gb_month_cap"
    domain: str = _DOMAIN
    description: str = (
        "Cloud egress GB/month cap. Self-hosted deployments leave this "
        "disabled (max_gb=0) — placeholder until egress telemetry "
        "wires up."
    )
    default_threshold: Mapping[str, Any] = field(
        default_factory=lambda: {"max_gb_per_month": 0}
    )

    def evaluate(self, state: Mapping[str, Any]) -> Severity | None:
        threshold = state.get("_threshold:" + self.id) or self.default_threshold
        cap = float(threshold.get("max_gb_per_month", 0) or 0)
        if cap <= 0:
            return None
        used = float((state.get("cost") or {}).get("egress_gb_month") or 0)
        if used >= cap:
            return "critical"
        if used >= cap * 0.85:
            return "warning"
        return None

    def remediate(self, state: Mapping[str, Any]) -> Action | None:
        return Action(rule_id=self.id, action="notify", ok=False,
                      detail="egress cap reached; alert billing owner")


register_guardrail(_EgressGbMonthCap())
