"""Guardrail-streak health-stories rule.

Pins the contract that the health-stories layer emits one warning-or-
critical story per guardrail that has fired warn+ for ≥ 2 consecutive
ticks.
"""

from __future__ import annotations

from media_stack.api.services.health_stories import (
    guardrail_streak_stories,
)


def test_no_streaks_no_stories():
    assert guardrail_streak_stories([]) == []


def test_below_min_streak_no_story():
    streaks = [{
        "rule_id": "storage:per_mount_threshold",
        "domain": "storage",
        "description": "x",
        "severity": "warning",
        "streak": 1,
    }]
    assert guardrail_streak_stories(streaks) == []


def test_two_tick_streak_emits_warning_story():
    streaks = [{
        "rule_id": "storage:per_mount_threshold",
        "domain": "storage",
        "description": "x",
        "severity": "warning",
        "streak": 2,
    }]
    out = guardrail_streak_stories(streaks)
    assert len(out) == 1
    assert out[0]["severity"] == "warning"
    assert out[0]["id"] == "guardrail-streak:storage:per_mount_threshold"


def test_critical_severity_promoted_to_critical_story():
    streaks = [{
        "rule_id": "auth:failed_login_spike",
        "domain": "auth",
        "description": "y",
        "severity": "critical",
        "streak": 3,
    }]
    out = guardrail_streak_stories(streaks)
    assert out[0]["severity"] == "critical"


def test_invalid_entries_skipped_gracefully():
    streaks = ["not a dict", None, {"rule_id": "", "streak": 5}]
    assert guardrail_streak_stories(streaks) == []
