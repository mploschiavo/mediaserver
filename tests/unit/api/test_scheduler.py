"""Unit tests for ``api.services.scheduler``.

Covers the v1.0.279 additions: ``enabled`` field with backfill,
``update_schedule``, ``set_schedule_enabled`` (pause/resume),
the ``MIN_INTERVAL_SECONDS`` guard rail, and ``get_due_actions``
respecting the enabled flag.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from media_stack.api.services import scheduler


@pytest.fixture(autouse=True)
def _isolate_schedules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Each test gets its own schedules.json so persisted state
    doesn't bleed across cases. ``_schedules_path`` re-resolves
    ``CONFIG_ROOT`` per call so the env override propagates."""
    monkeypatch.setenv("CONFIG_ROOT", str(tmp_path))
    yield


def _read_persisted(tmp_path: Path) -> list[dict]:
    """Read the on-disk schedules.json directly so tests can assert
    persistence beyond the in-memory return value."""
    f = tmp_path / ".controller" / "schedules.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else []


class TestAddSchedule:
    def test_creates_with_enabled_default_true(self) -> None:
        out = scheduler.add_schedule("scan", 300, "scan every 5m")
        assert out["status"] == "created"
        assert out["schedule"]["enabled"] is True
        assert out["schedule"]["action"] == "scan"
        assert out["schedule"]["interval_seconds"] == 300

    def test_creates_disabled_when_explicit(self) -> None:
        out = scheduler.add_schedule("scan", 300, "x", enabled=False)
        assert out["schedule"]["enabled"] is False

    def test_rejects_blank_action(self) -> None:
        out = scheduler.add_schedule("", 300)
        assert "error" in out

    def test_rejects_interval_below_minimum(self) -> None:
        out = scheduler.add_schedule("scan", 30)
        assert "error" in out
        assert str(scheduler.MIN_INTERVAL_SECONDS) in out["error"]


class TestGetSchedules:
    def test_backfills_enabled_for_legacy_entries(
        self, tmp_path: Path,
    ) -> None:
        # Simulate a schedule persisted before ``enabled`` existed.
        legacy = [
            {
                "id": 1,
                "action": "scan",
                "interval_seconds": 300,
                "label": "scan",
                "created_at": 1.0,
                "last_run": 0,
            },
        ]
        f = tmp_path / ".controller"
        f.mkdir(parents=True, exist_ok=True)
        (f / "schedules.json").write_text(json.dumps(legacy), encoding="utf-8")
        out = scheduler.get_schedules()
        assert out["count"] == 1
        assert out["schedules"][0]["enabled"] is True


class TestUpdateSchedule:
    def test_updates_only_supplied_fields(self) -> None:
        created = scheduler.add_schedule("scan", 300, "old")
        sid = created["schedule"]["id"]
        out = scheduler.update_schedule(sid, label="new")
        assert out["status"] == "updated"
        assert out["schedule"]["label"] == "new"
        # Untouched fields preserved.
        assert out["schedule"]["action"] == "scan"
        assert out["schedule"]["interval_seconds"] == 300

    def test_can_change_action_and_interval(self) -> None:
        created = scheduler.add_schedule("scan", 300, "x")
        sid = created["schedule"]["id"]
        out = scheduler.update_schedule(
            sid, action="probe", interval_seconds=600,
        )
        assert out["schedule"]["action"] == "probe"
        assert out["schedule"]["interval_seconds"] == 600

    def test_can_toggle_enabled(self) -> None:
        created = scheduler.add_schedule("scan", 300, "x")
        sid = created["schedule"]["id"]
        out = scheduler.update_schedule(sid, enabled=False)
        assert out["schedule"]["enabled"] is False
        out = scheduler.update_schedule(sid, enabled=True)
        assert out["schedule"]["enabled"] is True

    def test_returns_error_on_unknown_id(self) -> None:
        out = scheduler.update_schedule(99999)
        assert "error" in out

    def test_rejects_blank_action(self) -> None:
        created = scheduler.add_schedule("scan", 300, "x")
        sid = created["schedule"]["id"]
        out = scheduler.update_schedule(sid, action="")
        assert "error" in out

    def test_rejects_interval_below_minimum(self) -> None:
        created = scheduler.add_schedule("scan", 300, "x")
        sid = created["schedule"]["id"]
        out = scheduler.update_schedule(sid, interval_seconds=30)
        assert "error" in out

    def test_persists_changes_to_disk(self, tmp_path: Path) -> None:
        created = scheduler.add_schedule("scan", 300, "x")
        sid = created["schedule"]["id"]
        scheduler.update_schedule(sid, enabled=False, label="paused-scan")
        on_disk = _read_persisted(tmp_path)
        assert on_disk[0]["enabled"] is False
        assert on_disk[0]["label"] == "paused-scan"


class TestSetScheduleEnabled:
    def test_pause_then_resume(self) -> None:
        created = scheduler.add_schedule("scan", 300, "x")
        sid = created["schedule"]["id"]
        paused = scheduler.set_schedule_enabled(sid, enabled=False)
        assert paused["schedule"]["enabled"] is False
        resumed = scheduler.set_schedule_enabled(sid, enabled=True)
        assert resumed["schedule"]["enabled"] is True


class TestGetDueActions:
    def test_skips_disabled_schedules(self) -> None:
        created = scheduler.add_schedule("scan", 60, "x")
        sid = created["schedule"]["id"]
        scheduler.set_schedule_enabled(sid, enabled=False)
        due = scheduler.get_due_actions()
        assert due == []

    def test_returns_due_enabled_schedule(self) -> None:
        scheduler.add_schedule("scan", 60, "x")
        # Force last_run to 0 → due immediately.
        due = scheduler.get_due_actions()
        assert len(due) == 1
        assert due[0]["action"] == "scan"


class TestRemoveSchedule:
    def test_removes_known_id(self) -> None:
        created = scheduler.add_schedule("scan", 300, "x")
        sid = created["schedule"]["id"]
        out = scheduler.remove_schedule(sid)
        assert out["status"] == "removed"
        assert scheduler.get_schedules()["count"] == 0

    def test_returns_error_on_unknown_id(self) -> None:
        out = scheduler.remove_schedule(99999)
        assert "error" in out
