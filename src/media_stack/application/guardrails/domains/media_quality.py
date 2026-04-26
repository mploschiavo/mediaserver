"""Media-quality guardrails.

State expected:

- ``state["media_quality"]["duplicate_count"]`` — duplicates surfaced
  by the media-integrity service.
- ``state["media_quality"]["orphan_files"]`` — count of media files
  not referenced by any *arr.
- ``state["media_quality"]["stuck_imports"]`` — list of
  ``{age_hours: float}`` entries; the rule looks at the oldest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from media_stack.domain.guardrails.protocols import Action, Severity

from ..registry import register_guardrail


_DOMAIN = "media_quality"


@dataclass
class _DuplicateCount:
    id: str = "media:duplicate_count"
    domain: str = _DOMAIN
    description: str = (
        "Number of duplicates surfaced across radarr/sonarr/lidarr by the "
        "anti-duplicate engine. A high count means dedupe is falling behind."
    )
    default_threshold: Mapping[str, Any] = field(
        default_factory=lambda: {"max_count": 25}
    )

    def evaluate(self, state: Mapping[str, Any]) -> Severity | None:
        threshold = state.get("_threshold:" + self.id) or self.default_threshold
        cap = int(threshold.get("max_count", 25) or 0)
        if cap <= 0:
            return None
        n = int((state.get("media_quality") or {}).get("duplicate_count") or 0)
        if n >= cap:
            return "critical" if n >= cap * 4 else "warning"
        return None

    def remediate(self, state: Mapping[str, Any]) -> Action | None:
        return Action(rule_id=self.id, action="notify", ok=False,
                      detail="enqueue media-integrity reconcile pass")


@dataclass
class _OrphanFiles:
    id: str = "media:orphan_files"
    domain: str = _DOMAIN
    description: str = (
        "Files on disk that no *arr database references. A handful is "
        "fine; sustained growth signals a broken import pipeline."
    )
    default_threshold: Mapping[str, Any] = field(
        default_factory=lambda: {"max_count": 100}
    )

    def evaluate(self, state: Mapping[str, Any]) -> Severity | None:
        threshold = state.get("_threshold:" + self.id) or self.default_threshold
        cap = int(threshold.get("max_count", 100) or 0)
        if cap <= 0:
            return None
        n = int((state.get("media_quality") or {}).get("orphan_files") or 0)
        if n >= cap:
            return "warning"
        return None

    def remediate(self, state: Mapping[str, Any]) -> Action | None:
        return Action(rule_id=self.id, action="notify", ok=False,
                      detail="run orphan-file sweep")


@dataclass
class _StuckImportsAge:
    id: str = "media:stuck_imports_age"
    domain: str = _DOMAIN
    description: str = (
        "Oldest stuck import in the queue. If it's been there longer "
        "than the threshold, manual intervention is needed."
    )
    default_threshold: Mapping[str, Any] = field(
        default_factory=lambda: {"max_age_hours": 12}
    )

    def evaluate(self, state: Mapping[str, Any]) -> Severity | None:
        threshold = state.get("_threshold:" + self.id) or self.default_threshold
        max_h = float(threshold.get("max_age_hours", 12) or 0)
        if max_h <= 0:
            return None
        items = (state.get("media_quality") or {}).get("stuck_imports") or []
        if not isinstance(items, list):
            return None
        oldest = 0.0
        for entry in items:
            if not isinstance(entry, dict):
                continue
            try:
                age = float(entry.get("age_hours") or 0)
            except (TypeError, ValueError):
                continue
            if age > oldest:
                oldest = age
        if oldest > max_h:
            return "critical" if oldest > max_h * 2 else "warning"
        return None

    def remediate(self, state: Mapping[str, Any]) -> Action | None:
        return Action(rule_id=self.id, action="notify", ok=False,
                      detail="oldest stuck import past threshold; reset queue")

    def current_value(self, state: Mapping[str, Any]) -> float:
        items = (state.get("media_quality") or {}).get("stuck_imports") or []
        if not isinstance(items, list):
            return 0.0
        ages = []
        for entry in items:
            if isinstance(entry, dict):
                try:
                    ages.append(float(entry.get("age_hours") or 0))
                except (TypeError, ValueError):
                    continue
        return max(ages) if ages else 0.0


register_guardrail(_DuplicateCount())
register_guardrail(_OrphanFiles())
register_guardrail(_StuckImportsAge())
