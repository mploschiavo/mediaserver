"""Verify ``record_run_start`` / ``record_run_complete`` publish
``JobStarted`` / ``JobCompleted`` events on the process-wide bus.

The ``/api/events`` SSE handler subscribes to the same default bus
in production, so this test guarantees the wire of "controller wrote
a run record → operator's browser sees it tick" without spinning up
HTTP. Using ``subscribe_all`` keeps the assertion type-agnostic so
the test breaks loudly if either the event_type or the field set
drifts.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from media_stack.application.jobs import run_history
from media_stack.core.events import (
    JobCompleted,
    JobStarted,
    get_default_bus,
    reset_default_bus,
)
from media_stack.core.events.bus import Event
from media_stack.domain.jobs.run_record import RunStatus


@pytest.fixture(autouse=True)
def _isolate_bus_and_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Each test gets its own bus + run-history file so subscriber
    state and on-disk record counts don't bleed across cases."""
    reset_default_bus()
    # ``resolve_run_history_path`` reads ``CONFIG_ROOT`` per call, so
    # pointing it at a per-test tmp dir is sufficient — no module
    # reload needed.
    monkeypatch.setenv("CONFIG_ROOT", str(tmp_path))
    yield
    reset_default_bus()


def _capture_events() -> list[Event]:
    captured: list[Event] = []
    get_default_bus().subscribe_all(captured.append)
    return captured


class TestRecordRunStart:
    def test_publishes_job_started_with_record_fields(self) -> None:
        events = _capture_events()
        record = run_history.record_run_start(
            "scan-completed-downloads",
            triggered_by="cron",
            actor="alice",
        )
        started = [e for e in events if isinstance(e, JobStarted)]
        assert len(started) == 1, (
            f"expected exactly one JobStarted event, got {events!r}"
        )
        ev = started[0]
        assert ev.run_id == record.run_id
        assert ev.job_name == "scan-completed-downloads"
        assert ev.triggered_by == "cron"
        assert ev.actor == "alice"
        assert ev.event_type == "job.started"

    def test_does_not_publish_job_completed(self) -> None:
        events = _capture_events()
        run_history.record_run_start("scan", triggered_by="manual")
        assert not any(isinstance(e, JobCompleted) for e in events)


class TestRecordRunComplete:
    def test_publishes_job_completed_with_terminal_status(self) -> None:
        record = run_history.record_run_start("scan", triggered_by="cron")
        # Subscribe AFTER the start so we only capture the complete event.
        events: list[Event] = []
        get_default_bus().subscribe_all(events.append)
        run_history.record_run_complete(record.run_id, status=RunStatus.OK)
        completed = [e for e in events if isinstance(e, JobCompleted)]
        assert len(completed) == 1
        ev = completed[0]
        assert ev.run_id == record.run_id
        assert ev.job_name == "scan"
        assert ev.status == "ok"
        assert ev.elapsed >= 0
        assert ev.error == ""
        assert ev.event_type == "job.completed"

    def test_publishes_error_text_when_status_is_error(self) -> None:
        record = run_history.record_run_start("scan", triggered_by="cron")
        events: list[Event] = []
        get_default_bus().subscribe_all(events.append)
        run_history.record_run_complete(
            record.run_id, status=RunStatus.ERROR, error="boom",
        )
        completed = [e for e in events if isinstance(e, JobCompleted)]
        assert len(completed) == 1
        assert completed[0].status == "error"
        assert completed[0].error == "boom"

    def test_no_event_when_run_id_is_unknown(self) -> None:
        events: list[Event] = []
        get_default_bus().subscribe_all(events.append)
        result = run_history.record_run_complete(
            "01JNONEXISTENT00000000", status=RunStatus.OK,
        )
        assert result is None
        # The function returns early without writing OR publishing.
        assert not any(isinstance(e, JobCompleted) for e in events)
