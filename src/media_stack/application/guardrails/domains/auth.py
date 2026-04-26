"""Auth-domain guardrails.

State expected:

- ``state["auth"]["failed_login_tracker"]`` — snapshot from
  ``FailedLoginTracker.snapshot()``.
- ``state["auth"]["concurrent_sessions"]`` — int.
- ``state["auth"]["inactive_sessions"]`` — list of
  ``{session_id, idle_seconds, user}``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from media_stack.domain.guardrails.protocols import Action, Severity

from ..registry import register_guardrail


_DOMAIN = "auth"


@dataclass
class _FailedLoginSpike:
    id: str = "auth:failed_login_spike"
    domain: str = _DOMAIN
    description: str = (
        "Wraps the in-process FailedLoginTracker. Fires when any "
        "username has crossed the alert threshold within the window."
    )
    default_threshold: Mapping[str, Any] = field(
        default_factory=lambda: {"alert_count": 5}
    )

    def evaluate(self, state: Mapping[str, Any]) -> Severity | None:
        threshold = state.get("_threshold:" + self.id) or self.default_threshold
        cap = int(threshold.get("alert_count", 5) or 0)
        snap = (state.get("auth") or {}).get("failed_login_tracker") or {}
        if not isinstance(snap, dict) or cap <= 0:
            return None
        worst = 0
        any_alerted = False
        for window in snap.values():
            if not isinstance(window, dict):
                continue
            count = int(window.get("count") or 0)
            if window.get("alerted"):
                any_alerted = True
            if count > worst:
                worst = count
        if any_alerted or worst >= cap:
            return "critical" if worst >= cap * 2 else "warning"
        return None

    def remediate(self, state: Mapping[str, Any]) -> Action | None:
        return Action(rule_id=self.id, action="notify", ok=False,
                      detail="failed-login spike; review audit log")

    def current_value(self, state: Mapping[str, Any]) -> int:
        snap = (state.get("auth") or {}).get("failed_login_tracker") or {}
        if not isinstance(snap, dict):
            return 0
        return max(
            (int(w.get("count") or 0) for w in snap.values()
             if isinstance(w, dict)),
            default=0,
        )


@dataclass
class _ConcurrentSessionSpike:
    id: str = "auth:concurrent_session_spike"
    domain: str = _DOMAIN
    description: str = (
        "Hard ceiling on concurrent active sessions. Fires when the "
        "live count crosses the threshold — useful to spot credential "
        "sharing or token-replay attacks."
    )
    default_threshold: Mapping[str, Any] = field(
        default_factory=lambda: {"max_concurrent": 25}
    )

    def evaluate(self, state: Mapping[str, Any]) -> Severity | None:
        threshold = state.get("_threshold:" + self.id) or self.default_threshold
        cap = int(threshold.get("max_concurrent", 25) or 0)
        if cap <= 0:
            return None
        n = int((state.get("auth") or {}).get("concurrent_sessions") or 0)
        if n > cap:
            return "warning"
        return None

    def remediate(self, state: Mapping[str, Any]) -> Action | None:
        return Action(rule_id=self.id, action="notify", ok=False,
                      detail="concurrent-session spike; require step-up auth")


@dataclass
class _SessionInactivity:
    id: str = "auth:session_inactivity"
    domain: str = _DOMAIN
    description: str = (
        "Auto-revokes sessions that haven't seen activity in the "
        "configured idle window. Defence against unattended consoles."
    )
    default_threshold: Mapping[str, Any] = field(
        default_factory=lambda: {"max_idle_minutes": 60}
    )

    def evaluate(self, state: Mapping[str, Any]) -> Severity | None:
        threshold = state.get("_threshold:" + self.id) or self.default_threshold
        max_idle_s = float(threshold.get("max_idle_minutes", 60) or 0) * 60.0
        if max_idle_s <= 0:
            return None
        sessions = (state.get("auth") or {}).get("inactive_sessions") or []
        if not isinstance(sessions, list):
            return None
        for s in sessions:
            if not isinstance(s, dict):
                continue
            try:
                idle = float(s.get("idle_seconds") or 0)
            except (TypeError, ValueError):
                continue
            if idle > max_idle_s:
                return "warning"
        return None

    def remediate(self, state: Mapping[str, Any]) -> Action | None:
        # The session store handles the actual revoke; we record the
        # intent so the audit log knows the revoke was guardrail-driven.
        return Action(rule_id=self.id, action="revoke_idle_sessions",
                      ok=True, detail="auto-revoke idle sessions")


register_guardrail(_FailedLoginSpike())
register_guardrail(_ConcurrentSessionSpike())
register_guardrail(_SessionInactivity())
