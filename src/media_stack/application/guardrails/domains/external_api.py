"""External API quota guardrails.

State expected:

- ``state["external_api"]["opensubtitles_used"]`` — count of API
  calls in current window (rolling 24h).
- ``state["external_api"]["tmdb_calls_today"]`` — running counter.
- ``state["external_api"]["indexer_429s"]`` — same shape the
  bandwidth domain consumes; counted here to surface ban-risk
  separately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from media_stack.domain.guardrails.protocols import Action, Severity

from ..registry import register_guardrail


_DOMAIN = "external_api"


@dataclass
class _OpenSubtitlesQuota:
    id: str = "api:opensubtitles_quota"
    domain: str = _DOMAIN
    description: str = (
        "Tracks OpenSubtitles daily download quota. Free tier is 5/day, "
        "VIP 1000/day; the rule fires before the burn-out so subtitle "
        "search keeps working for the rest of the day."
    )
    default_threshold: Mapping[str, Any] = field(
        default_factory=lambda: {"max_per_day": 200, "warn_at_percent": 80}
    )

    def evaluate(self, state: Mapping[str, Any]) -> Severity | None:
        threshold = state.get("_threshold:" + self.id) or self.default_threshold
        cap = int(threshold.get("max_per_day", 200) or 0)
        warn_pct = float(threshold.get("warn_at_percent", 80) or 80)
        if cap <= 0:
            return None
        used = int((state.get("external_api") or {}).get("opensubtitles_used") or 0)
        if used >= cap:
            return "critical"
        if used >= cap * (warn_pct / 100.0):
            return "warning"
        return None

    def remediate(self, state: Mapping[str, Any]) -> Action | None:
        return Action(rule_id=self.id, action="notify", ok=False,
                      detail="opensubtitles near quota; pause subtitle pulls")

    def current_value(self, state: Mapping[str, Any]) -> int:
        return int((state.get("external_api") or {}).get("opensubtitles_used") or 0)


@dataclass
class _TmdbCallBudget:
    id: str = "api:tmdb_call_budget"
    domain: str = _DOMAIN
    description: str = (
        "TMDB has a 50 req/sec rolling cap. This rule tracks daily "
        "budget so a runaway scrape doesn't get the API key flagged."
    )
    default_threshold: Mapping[str, Any] = field(
        default_factory=lambda: {"max_calls_per_day": 100000}
    )

    def evaluate(self, state: Mapping[str, Any]) -> Severity | None:
        threshold = state.get("_threshold:" + self.id) or self.default_threshold
        cap = int(threshold.get("max_calls_per_day", 100000) or 0)
        if cap <= 0:
            return None
        used = int((state.get("external_api") or {}).get("tmdb_calls_today") or 0)
        if used >= cap:
            return "critical"
        if used >= cap * 0.85:
            return "warning"
        return None

    def remediate(self, state: Mapping[str, Any]) -> Action | None:
        return Action(rule_id=self.id, action="notify", ok=False,
                      detail="tmdb call budget warning")


@dataclass
class _IndexerBanRisk:
    id: str = "api:indexer_ban_risk"
    domain: str = _DOMAIN
    description: str = (
        "Aggregates 429 responses from indexers across the last hour; "
        "high counts predict an upcoming IP-level ban."
    )
    default_threshold: Mapping[str, Any] = field(
        default_factory=lambda: {"max_429s_per_hour": 30}
    )

    def evaluate(self, state: Mapping[str, Any]) -> Severity | None:
        threshold = state.get("_threshold:" + self.id) or self.default_threshold
        cap = int(threshold.get("max_429s_per_hour", 30) or 0)
        events = (state.get("external_api") or {}).get("indexer_429s") or []
        if cap <= 0 or not isinstance(events, list):
            return None
        n = len(events)
        if n >= cap:
            return "critical" if n >= cap * 2 else "warning"
        return None

    def remediate(self, state: Mapping[str, Any]) -> Action | None:
        return Action(rule_id=self.id, action="cooldown", ok=False,
                      detail="back off all indexers globally for 15 min")


register_guardrail(_OpenSubtitlesQuota())
register_guardrail(_TmdbCallBudget())
register_guardrail(_IndexerBanRisk())
