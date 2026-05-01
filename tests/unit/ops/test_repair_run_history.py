"""Tests for the run-history repair tool.

Logic lives in ``media_stack.application.jobs.run_history_repair``
(canonical, importable). ``bin/ops/repair_run_history.py`` is a
thin operator CLI wrapper. Tests target the module.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


def _load_module():
    """Convenience accessor — kept as a function for back-compat
    with the prior importlib-based pattern, but it's now just a
    normal import. Future tests should import the module directly."""
    from media_stack.application.jobs import run_history_repair

    return run_history_repair


@pytest.fixture
def repair_module():
    return _load_module()


def _write_history(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for r in records:
            handle.write(json.dumps(r))
            handle.write("\n")


def _read_history(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


class TestFixStuckRunning:
    """The default scenario — the only one that mutates running state."""

    def test_dry_run_does_not_touch_file(self, repair_module, tmp_path: Path) -> None:
        history = tmp_path / "run-history.jsonl"
        now = 1_000_000.0
        _write_history(history, [
            {
                "run_id": "01ABC",
                "job_name": "mass-search-throttled",
                "status": "running",
                "started_at": now - 7200,  # 2 hours ago
            },
        ])
        report = repair_module.run_repair(
            history_path=history,
            apply=False,
            older_than_seconds=600,
            mark_as=repair_module.STATUS_ERROR,
            scenarios=[repair_module.SCENARIO_FIX_STUCK_RUNNING],
            backup=False,
            now=now,
        )
        assert len(report.actions) == 1
        # File still says running — apply=False is read-only.
        assert _read_history(history)[0]["status"] == "running"

    def test_apply_marks_stale_running_records_as_terminal(
        self, repair_module, tmp_path: Path,
    ) -> None:
        history = tmp_path / "run-history.jsonl"
        now = 1_000_000.0
        _write_history(history, [
            {
                "run_id": "01ABC",
                "job_name": "mass-search-throttled",
                "status": "running",
                "started_at": now - 7200,
            },
            {
                "run_id": "01DEF",
                "job_name": "guardrails:evaluate",
                "status": "ok",
                "started_at": now - 3600,
                "completed_at": now - 3590,
                "elapsed": 10.0,
            },
        ])
        report = repair_module.run_repair(
            history_path=history,
            apply=True,
            older_than_seconds=600,
            mark_as=repair_module.STATUS_ERROR,
            scenarios=[repair_module.SCENARIO_FIX_STUCK_RUNNING],
            backup=False,
            now=now,
        )
        assert len(report.actions) == 1
        rows = _read_history(history)
        stuck = next(r for r in rows if r["run_id"] == "01ABC")
        assert stuck["status"] == "error"
        assert stuck["completed_at"] == pytest.approx(now)
        assert stuck["elapsed"] == pytest.approx(7200.0)
        assert "repair_run_history" in stuck["error"]
        # Untouched record stays untouched.
        ok = next(r for r in rows if r["run_id"] == "01DEF")
        assert ok["status"] == "ok"
        assert ok["elapsed"] == 10.0

    def test_recent_running_records_are_skipped(
        self, repair_module, tmp_path: Path,
    ) -> None:
        history = tmp_path / "run-history.jsonl"
        now = 1_000_000.0
        _write_history(history, [
            {
                "run_id": "01ABC",
                "job_name": "fresh-job",
                "status": "running",
                "started_at": now - 60,  # 1 min — under the 10 min threshold
            },
        ])
        report = repair_module.run_repair(
            history_path=history,
            apply=True,
            older_than_seconds=600,
            mark_as=repair_module.STATUS_ERROR,
            scenarios=[repair_module.SCENARIO_FIX_STUCK_RUNNING],
            backup=False,
            now=now,
        )
        assert report.actions == []
        assert report.skipped_recent_running == 1
        assert _read_history(history)[0]["status"] == "running"

    def test_mark_as_cancelled_writes_cancelled_status(
        self, repair_module, tmp_path: Path,
    ) -> None:
        history = tmp_path / "run-history.jsonl"
        now = 1_000_000.0
        _write_history(history, [
            {
                "run_id": "01ABC",
                "job_name": "mass-search-throttled",
                "status": "running",
                "started_at": now - 7200,
            },
        ])
        repair_module.run_repair(
            history_path=history,
            apply=True,
            older_than_seconds=600,
            mark_as=repair_module.STATUS_CANCELLED,
            scenarios=[repair_module.SCENARIO_FIX_STUCK_RUNNING],
            backup=False,
            now=now,
        )
        assert _read_history(history)[0]["status"] == "cancelled"

    def test_idempotent_second_run_does_nothing(
        self, repair_module, tmp_path: Path,
    ) -> None:
        history = tmp_path / "run-history.jsonl"
        now = 1_000_000.0
        _write_history(history, [
            {
                "run_id": "01ABC",
                "job_name": "mass-search-throttled",
                "status": "running",
                "started_at": now - 7200,
            },
        ])
        kwargs = dict(
            history_path=history,
            apply=True,
            older_than_seconds=600,
            mark_as=repair_module.STATUS_ERROR,
            scenarios=[repair_module.SCENARIO_FIX_STUCK_RUNNING],
            backup=False,
            now=now,
        )
        first = repair_module.run_repair(**kwargs)
        second = repair_module.run_repair(**kwargs)
        assert len(first.actions) == 1
        assert second.actions == []  # nothing left to fix


class TestBackfillElapsed:
    def test_fills_missing_elapsed_for_terminal_records(
        self, repair_module, tmp_path: Path,
    ) -> None:
        history = tmp_path / "run-history.jsonl"
        now = 1_000_000.0
        _write_history(history, [
            {
                "run_id": "01ABC",
                "job_name": "x",
                "status": "ok",
                "started_at": now - 100,
                "completed_at": now - 90,
            },
            {
                "run_id": "01DEF",
                "job_name": "y",
                "status": "ok",
                "started_at": now - 50,
                "completed_at": now - 40,
                "elapsed": 10.0,  # already set; should be skipped
            },
        ])
        report = repair_module.run_repair(
            history_path=history,
            apply=True,
            older_than_seconds=600,
            mark_as=repair_module.STATUS_ERROR,
            scenarios=[repair_module.SCENARIO_BACKFILL_ELAPSED],
            backup=False,
            now=now,
        )
        assert len(report.actions) == 1
        rows = _read_history(history)
        filled = next(r for r in rows if r["run_id"] == "01ABC")
        assert filled["elapsed"] == pytest.approx(10.0)


class TestBackup:
    def test_backup_is_written_when_apply_changes(
        self, repair_module, tmp_path: Path,
    ) -> None:
        history = tmp_path / "run-history.jsonl"
        now = 1_000_000.0
        _write_history(history, [
            {
                "run_id": "01ABC",
                "job_name": "x",
                "status": "running",
                "started_at": now - 7200,
            },
        ])
        report = repair_module.run_repair(
            history_path=history,
            apply=True,
            older_than_seconds=600,
            mark_as=repair_module.STATUS_ERROR,
            scenarios=[repair_module.SCENARIO_FIX_STUCK_RUNNING],
            backup=True,
            now=now,
        )
        assert report.backup_path is not None
        assert Path(report.backup_path).is_file()

    def test_no_backup_when_no_changes(
        self, repair_module, tmp_path: Path,
    ) -> None:
        history = tmp_path / "run-history.jsonl"
        _write_history(history, [
            {
                "run_id": "01ABC",
                "job_name": "x",
                "status": "ok",
                "started_at": 1.0,
                "completed_at": 2.0,
                "elapsed": 1.0,
            },
        ])
        report = repair_module.run_repair(
            history_path=history,
            apply=True,
            older_than_seconds=600,
            mark_as=repair_module.STATUS_ERROR,
            scenarios=[repair_module.SCENARIO_FIX_STUCK_RUNNING],
            backup=True,
        )
        assert report.actions == []
        assert report.backup_path is None


class TestCli:
    def test_unknown_scenario_errors_out(self, repair_module) -> None:
        with pytest.raises(SystemExit):
            repair_module.parse_scenarios("not-a-real-scenario")

    def test_default_scenario_list_when_blank(self, repair_module) -> None:
        assert (
            repair_module.parse_scenarios("")
            == list(repair_module.DEFAULT_SCENARIOS)
        )

    def test_resolve_history_path_explicit(
        self, repair_module, tmp_path: Path,
    ) -> None:
        history = tmp_path / "run-history.jsonl"
        history.write_text("")
        resolved = repair_module.resolve_history_path(str(history))
        assert resolved == history.resolve()

    def test_resolve_history_path_missing_explicit_errors(
        self, repair_module, tmp_path: Path,
    ) -> None:
        with pytest.raises(FileNotFoundError):
            repair_module.resolve_history_path(str(tmp_path / "missing"))


def test_smoke_main_with_apply_against_temp_file(
    repair_module, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """End-to-end: call ``main`` like the CLI does. Confirms argparse
    wiring + summary output."""
    history = tmp_path / "run-history.jsonl"
    now = time.time()
    _write_history(history, [
        {
            "run_id": "01ABC",
            "job_name": "mass-search-throttled",
            "status": "running",
            "started_at": now - 7200,
        },
    ])
    rc = repair_module.main([
        "--history-path", str(history),
        "--apply",
        "--no-backup",
        "--json",
    ])
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["apply"] is True
    assert payload["actions_count"] == 1
    assert payload["actions"][0]["scenario"] == "fix-stuck-running"
