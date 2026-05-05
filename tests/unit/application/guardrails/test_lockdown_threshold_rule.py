"""Unit tests for the ``_LockdownThreshold`` rule + the
``evaluation_loop.tick`` dispatcher hook (ADR-0008 Phase 1).

The rule is exercised directly (no registry round-trip) so the
state-shape contract is what's under test. The dispatcher test uses
the real ``tick()`` with a hand-rolled state dict so the wiring
between rule → action → service is end-to-end checked without
needing the live ``collect_state`` path.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest


# Ensure src is on the path the same way the existing guardrails tests
# do (matches tests/guardrails/conftest.py's path bootstrap).
ROOT = Path(__file__).resolve().parents[4]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


from media_stack.application.guardrails.domains.storage import (  # noqa: E402
    _LockdownThreshold,
)
from media_stack.application.guardrails import evaluation_loop  # noqa: E402
from media_stack.application.guardrails.registry import (  # noqa: E402
    GuardrailRegistry,
)
from media_stack.domain.guardrails.protocols import Action  # noqa: E402


def _state(
    *,
    disks: dict[str, dict[str, float]],
    engaged: bool = False,
    trigger: str | None = None,
    paused_clients: list[str] | None = None,
    threshold_override: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Hand-roll the snapshot the rule + dispatcher see."""
    state: dict[str, Any] = {
        "disk": disks,
        "_lockdown_state": {
            "engaged": engaged,
            "trigger": trigger,
            "paused_clients": list(paused_clients or []),
        },
    }
    if threshold_override is not None:
        state["_threshold:storage:lockdown_threshold"] = threshold_override
    return state


class TestLockdownThresholdEvaluate:
    def test_critical_when_disk_over_lockdown_and_not_engaged(self) -> None:
        rule = _LockdownThreshold()
        state = _state(disks={"media": {"percent_used": 80.0}})
        assert rule.evaluate(state) == "critical"

    def test_silent_when_disk_under_lockdown_and_not_engaged(self) -> None:
        rule = _LockdownThreshold()
        state = _state(disks={"media": {"percent_used": 50.0}})
        assert rule.evaluate(state) is None

    def test_info_when_engaged_auto_and_disk_under_release(self) -> None:
        """Auto-engaged + every mount under release_percent ⇒ ``info``
        signals the auto-release path is ready to fire."""
        rule = _LockdownThreshold()
        state = _state(
            disks={"media": {"percent_used": 55.0}},
            engaged=True,
            trigger="auto",
        )
        assert rule.evaluate(state) == "info"

    def test_warning_when_engaged_auto_and_disk_still_over_release(
        self,
    ) -> None:
        rule = _LockdownThreshold()
        state = _state(
            disks={"media": {"percent_used": 65.0}},
            engaged=True,
            trigger="auto",
        )
        assert rule.evaluate(state) == "warning"

    def test_warning_when_engaged_manual_and_disk_recovered(self) -> None:
        """Manual stickiness: even fully recovered, manual lockdowns
        stay warning until the operator releases."""
        rule = _LockdownThreshold()
        state = _state(
            disks={"media": {"percent_used": 30.0}},
            engaged=True,
            trigger="manual",
        )
        assert rule.evaluate(state) == "warning"

    def test_critical_when_engaged_manual_and_disk_still_over_lockdown(
        self,
    ) -> None:
        rule = _LockdownThreshold()
        state = _state(
            disks={"media": {"percent_used": 90.0}},
            engaged=True,
            trigger="manual",
        )
        assert rule.evaluate(state) == "critical"

    def test_threshold_override_lowers_lockdown_bar(self) -> None:
        """Operator override via ``_threshold:storage:lockdown_threshold``
        — pushing lockdown down to 50% makes a 60% disk fire critical
        when it would otherwise be silent under defaults."""
        rule = _LockdownThreshold()
        state = _state(
            disks={"media": {"percent_used": 60.0}},
            threshold_override={
                "lockdown_percent": 50.0,
                "release_percent": 40.0,
            },
        )
        assert rule.evaluate(state) == "critical"

    def test_threshold_override_raises_release_bar(self) -> None:
        rule = _LockdownThreshold()
        state = _state(
            disks={"media": {"percent_used": 70.0}},
            engaged=True,
            trigger="auto",
            threshold_override={
                "lockdown_percent": 80.0,
                "release_percent": 75.0,
            },
        )
        # 70 < 75 release bar ⇒ auto-release path ready.
        assert rule.evaluate(state) == "info"

    def test_misconfigured_release_at_or_above_lockdown_clamps(
        self,
    ) -> None:
        """If the operator sets release_percent >= lockdown_percent,
        the rule clamps release down to ``lockdown - 1`` so it never
        engages and immediately auto-releases on the same tick."""
        rule = _LockdownThreshold()
        state = _state(
            disks={"media": {"percent_used": 85.0}},
            threshold_override={
                "lockdown_percent": 75.0,
                "release_percent": 80.0,
            },
        )
        # 85 > 75 lockdown bar ⇒ critical (engage path).
        assert rule.evaluate(state) == "critical"

    def test_silent_when_disk_state_missing(self) -> None:
        rule = _LockdownThreshold()
        # No "disk" key at all.
        assert rule.evaluate({"_lockdown_state": {"engaged": False}}) is None

    def test_silent_when_engaged_state_corrupt(self) -> None:
        """If ``_lockdown_state`` is a non-Mapping (corrupt / wrong
        type), the rule must default to "not engaged" and behave the
        same as a fresh install."""
        rule = _LockdownThreshold()
        state = {
            "disk": {"media": {"percent_used": 50.0}},
            "_lockdown_state": "not-a-dict",
        }
        assert rule.evaluate(state) is None


class TestLockdownThresholdRemediate:
    def test_engage_action_when_critical_and_not_engaged(self) -> None:
        rule = _LockdownThreshold()
        state = _state(disks={"media": {"percent_used": 80.0}})
        action = rule.remediate(state)
        assert isinstance(action, Action)
        assert action.action == "lockdown_engage"
        assert action.ok is False
        assert "media" in action.detail
        assert "80" in action.detail or "80.0" in action.detail

    def test_release_action_when_engaged_auto_and_disk_recovered(
        self,
    ) -> None:
        rule = _LockdownThreshold()
        state = _state(
            disks={"media": {"percent_used": 55.0}},
            engaged=True,
            trigger="auto",
        )
        action = rule.remediate(state)
        assert isinstance(action, Action)
        assert action.action == "lockdown_release"
        assert action.ok is False

    def test_no_action_when_engaged_manual(self) -> None:
        """Manual lockdowns never auto-release, so ``remediate``
        returns ``None`` even when disk is recovered. The rule's
        evaluate still returns ``warning`` so the UI surfaces it."""
        rule = _LockdownThreshold()
        state = _state(
            disks={"media": {"percent_used": 30.0}},
            engaged=True,
            trigger="manual",
        )
        assert rule.remediate(state) is None

    def test_no_action_when_engaged_auto_still_over_release(self) -> None:
        rule = _LockdownThreshold()
        state = _state(
            disks={"media": {"percent_used": 65.0}},
            engaged=True,
            trigger="auto",
        )
        assert rule.remediate(state) is None

    def test_no_action_when_silent_and_below_lockdown(self) -> None:
        rule = _LockdownThreshold()
        state = _state(disks={"media": {"percent_used": 40.0}})
        assert rule.remediate(state) is None


class _FakeLockdownService:
    """Minimal duck-typed stand-in matching the parts of
    ``DownloadLockdownService`` the dispatcher invokes."""

    def __init__(self) -> None:
        self.engage_calls: list[dict[str, Any]] = []
        self.release_calls: list[dict[str, Any]] = []
        self.engage_result: dict[str, Any] = {
            "paused_clients": ["qbittorrent", "sabnzbd"],
            "failures": [],
            "engaged": True,
            "trigger": "auto",
            "already_engaged": False,
        }
        self.release_result: dict[str, Any] = {
            "released_clients": ["qbittorrent", "sabnzbd"],
            "failures": [],
            "engaged": False,
            "was_engaged": True,
        }

    def engage(self, *, trigger: str, by: str) -> dict[str, Any]:
        self.engage_calls.append({"trigger": trigger, "by": by})
        return self.engage_result

    def release(self, *, by: str) -> dict[str, Any]:
        self.release_calls.append({"by": by})
        return self.release_result


class TestEvaluationLoopDispatch:
    def _build_registry(self) -> GuardrailRegistry:
        """Build a minimal registry containing only the lockdown
        rule so other rules can't fire and pollute the action list."""
        reg = GuardrailRegistry(
            override_path_fn=lambda: Path("/tmp/.unused-lockdown-tests.json"),
        )
        reg.register(_LockdownThreshold())
        return reg

    def test_dispatcher_invokes_engage_on_lockdown_engage_action(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Reset cadence throttle so the test always runs.
        monkeypatch.setattr(evaluation_loop, "_last_tick_at", 0.0)
        reg = self._build_registry()
        svc = _FakeLockdownService()
        state = _state(disks={"media": {"percent_used": 80.0}})

        result = evaluation_loop.tick(
            registry=reg,
            state=state,
            record_history=False,
            min_interval=0,
            lockdown_service=svc,
        )

        # The rule fired critical → action ran → service.engage called.
        assert len(svc.engage_calls) == 1
        assert svc.engage_calls[0]["trigger"] == "auto"
        assert svc.engage_calls[0]["by"].startswith("auto:disk-")
        # The dispatched action's ok flips True; detail describes
        # what actually happened.
        actions = result["actions"]
        engage_acts = [a for a in actions if a["action"] == "lockdown_engage"]
        assert len(engage_acts) == 1
        assert engage_acts[0]["ok"] is True
        assert "paused 2 clients" in engage_acts[0]["detail"]

    def test_dispatcher_invokes_release_on_lockdown_release_action(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(evaluation_loop, "_last_tick_at", 0.0)
        reg = self._build_registry()
        svc = _FakeLockdownService()
        state = _state(
            disks={"media": {"percent_used": 55.0}},
            engaged=True,
            trigger="auto",
        )

        result = evaluation_loop.tick(
            registry=reg,
            state=state,
            record_history=False,
            min_interval=0,
            lockdown_service=svc,
        )

        assert len(svc.release_calls) == 1
        assert svc.release_calls[0]["by"] == "auto:disk-recovered"
        actions = result["actions"]
        release_acts = [a for a in actions if a["action"] == "lockdown_release"]
        assert len(release_acts) == 1
        assert release_acts[0]["ok"] is True
        assert "released 2 clients" in release_acts[0]["detail"]

    def test_dispatcher_skips_when_no_service_registered(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without a service the dispatcher must NOT raise — it logs
        + leaves the action untouched (ok=False)."""
        monkeypatch.setattr(evaluation_loop, "_last_tick_at", 0.0)
        reg = self._build_registry()
        state = _state(disks={"media": {"percent_used": 80.0}})

        result = evaluation_loop.tick(
            registry=reg,
            state=state,
            record_history=False,
            min_interval=0,
            lockdown_service=None,
        )

        engage_acts = [
            a for a in result["actions"]
            if a["action"] == "lockdown_engage"
        ]
        assert len(engage_acts) == 1
        # No service → ok stays False (the rule's pre-dispatch shape).
        assert engage_acts[0]["ok"] is False

    def test_dispatcher_handles_engage_with_zero_paused_clients(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If every adapter failed to pause, the engage result has
        an empty ``paused_clients`` and a populated ``failures``
        list. The dispatcher records ok=False so the audit log
        surfaces the problem."""
        monkeypatch.setattr(evaluation_loop, "_last_tick_at", 0.0)
        reg = self._build_registry()
        svc = _FakeLockdownService()
        svc.engage_result = {
            "paused_clients": [],
            "failures": [
                {"client": "qbittorrent", "action": "pause"},
                {"client": "sabnzbd", "action": "pause"},
            ],
            "engaged": True,
            "trigger": "auto",
            "already_engaged": False,
        }
        state = _state(disks={"media": {"percent_used": 80.0}})

        result = evaluation_loop.tick(
            registry=reg,
            state=state,
            record_history=False,
            min_interval=0,
            lockdown_service=svc,
        )
        engage_acts = [
            a for a in result["actions"]
            if a["action"] == "lockdown_engage"
        ]
        assert engage_acts[0]["ok"] is False
        assert "paused 0 clients" in engage_acts[0]["detail"]
        assert "failures=2" in engage_acts[0]["detail"]

    def test_dispatcher_treats_already_engaged_as_ok(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(evaluation_loop, "_last_tick_at", 0.0)
        reg = self._build_registry()
        svc = _FakeLockdownService()
        svc.engage_result = {
            "paused_clients": ["qbittorrent"],
            "failures": [],
            "engaged": True,
            "trigger": "auto",
            "already_engaged": True,
        }
        state = _state(disks={"media": {"percent_used": 80.0}})

        result = evaluation_loop.tick(
            registry=reg,
            state=state,
            record_history=False,
            min_interval=0,
            lockdown_service=svc,
        )
        engage_acts = [
            a for a in result["actions"]
            if a["action"] == "lockdown_engage"
        ]
        assert engage_acts[0]["ok"] is True
        assert "already engaged" in engage_acts[0]["detail"]
