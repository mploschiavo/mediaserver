"""Bandwidth-domain guardrails.

State expected:

- ``state["bandwidth"]["upload_gb_today"]`` — running counter of
  outbound bytes (placeholder when the egress collector isn't wired).
- ``state["bandwidth"]["concurrent_downloads"]`` — count from qBit /
  SAB.
- ``state["bandwidth"]["indexer_429s"]`` — list of (indexer_id, ts)
  tuples within the last 5 minutes, populated by the in-process 429
  tracker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from media_stack.domain.guardrails.protocols import Action, Severity

from ..registry import register_guardrail


_DOMAIN = "bandwidth"


@dataclass
class _DailyUploadCap:
    id: str = "bandwidth:daily_upload_cap"
    domain: str = _DOMAIN
    description: str = (
        "Caps daily torrent upload to avoid blowing past the user's ISP "
        "limit. Throttles qBit when the cap is breached."
    )
    default_threshold: Mapping[str, Any] = field(
        default_factory=lambda: {"max_gb_per_day": 0}  # 0 = disabled
    )

    def evaluate(self, state: Mapping[str, Any]) -> Severity | None:
        threshold = state.get("_threshold:" + self.id) or self.default_threshold
        cap = float(threshold.get("max_gb_per_day", 0) or 0)
        if cap <= 0:
            return None
        used = float(
            (state.get("bandwidth") or {}).get("upload_gb_today") or 0
        )
        if used >= cap:
            return "critical" if used >= cap * 1.1 else "warning"
        return None

    def remediate(self, state: Mapping[str, Any]) -> Action | None:
        return Action(
            rule_id=self.id, action="throttle", ok=False,
            detail="cap exceeded; throttle qBit upload",
        )

    def current_value(self, state: Mapping[str, Any]) -> float:
        return float(
            (state.get("bandwidth") or {}).get("upload_gb_today") or 0
        )


@dataclass
class _ConcurrentDownloadsCap:
    id: str = "bandwidth:concurrent_downloads_cap"
    domain: str = _DOMAIN
    description: str = (
        "Limits how many downloads can run concurrently. Too many in "
        "flight causes thrashing and indexer rate-limit cascades."
    )
    default_threshold: Mapping[str, Any] = field(
        default_factory=lambda: {"max_concurrent": 8}
    )

    def evaluate(self, state: Mapping[str, Any]) -> Severity | None:
        threshold = state.get("_threshold:" + self.id) or self.default_threshold
        cap = int(threshold.get("max_concurrent", 8) or 0)
        if cap <= 0:
            return None
        running = int(
            (state.get("bandwidth") or {}).get("concurrent_downloads") or 0
        )
        if running > cap:
            return "warning"
        return None

    def remediate(self, state: Mapping[str, Any]) -> Action | None:
        return Action(
            rule_id=self.id, action="throttle", ok=False,
            detail="reduce qBit/SAB max-active to bring under cap",
        )

    def current_value(self, state: Mapping[str, Any]) -> int:
        return int(
            (state.get("bandwidth") or {}).get("concurrent_downloads") or 0
        )


@dataclass
class _Indexer429Window:
    id: str = "bandwidth:indexer_429_window"
    domain: str = _DOMAIN
    description: str = (
        "Counts 429s from any single indexer in the last 5 minutes. A "
        "burst signals the stack is about to be banned."
    )
    default_threshold: Mapping[str, Any] = field(
        default_factory=lambda: {"max_429s_per_window": 6}
    )

    def evaluate(self, state: Mapping[str, Any]) -> Severity | None:
        threshold = state.get("_threshold:" + self.id) or self.default_threshold
        cap = int(threshold.get("max_429s_per_window", 6) or 0)
        events = (state.get("bandwidth") or {}).get("indexer_429s") or []
        if cap <= 0 or not isinstance(events, list):
            return None
        # Bucket by indexer id (first element of the tuple/dict).
        counts: dict[str, int] = {}
        for ev in events:
            if isinstance(ev, dict):
                key = str(ev.get("indexer") or "")
            elif isinstance(ev, (list, tuple)) and ev:
                key = str(ev[0])
            else:
                continue
            counts[key] = counts.get(key, 0) + 1
        for n in counts.values():
            if n >= cap:
                return "critical" if n >= cap * 2 else "warning"
        return None

    def remediate(self, state: Mapping[str, Any]) -> Action | None:
        return Action(
            rule_id=self.id, action="cooldown", ok=False,
            detail="back off offending indexer(s) for cooldown window",
        )


register_guardrail(_DailyUploadCap())
register_guardrail(_ConcurrentDownloadsCap())
register_guardrail(_Indexer429Window())
