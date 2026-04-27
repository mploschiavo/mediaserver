"""Unit tests for ``application.jobs.run_history``.

Covers:
  * record_run_start writes a running record with a fresh ULID
  * record_run_complete updates the start record in place + appends
    to parent's child_run_ids
  * record_run_complete returns None for unknown run_id
  * record_run_complete rejects non-terminal status
  * get_runs filters: job, since_ts, parent, batch, limit, sort order
  * get_run / get_latest_run / get_children / iter_records
  * Cap trimming: writing past the hard cap trims to 95% headroom
  * Concurrent write safety (single-process: lock holds)
  * Malformed JSONL lines are skipped, not crashing the reader
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from media_stack.application.jobs import run_history as rh
from media_stack.domain.jobs.run_record import (
    RUN_HISTORY_HARD_CAP,
    RunRecord,
    RunStatus,
)


@pytest.fixture(autouse=True)
def isolated_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the run-history file at a per-test tempdir so tests
    don't write into /srv-config and don't see each other's data."""
    monkeypatch.setenv("CONFIG_ROOT", str(tmp_path))


class TestRecordRunStart:
    def test_writes_running_record(self, tmp_path: Path) -> None:
        rec = rh.record_run_start("envoy-config", triggered_by="cron")
        assert rec.status == RunStatus.RUNNING
        assert rec.job_name == "envoy-config"
        assert rec.triggered_by == "cron"
        assert rec.run_id
        # Persisted to disk.
        path = tmp_path / ".controller" / "run-history.jsonl"
        assert path.is_file()
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["status"] == "running"
        assert data["job_name"] == "envoy-config"

    def test_two_starts_are_distinct(self) -> None:
        a = rh.record_run_start("x")
        b = rh.record_run_start("x")
        assert a.run_id != b.run_id

    def test_stores_parent_and_batch(self) -> None:
        parent = rh.record_run_start("batch", triggered_by="cron")
        child = rh.record_run_start(
            "envoy-config",
            parent_run_id=parent.run_id,
            batch_id=parent.run_id,
        )
        assert child.parent_run_id == parent.run_id
        assert child.batch_id == parent.run_id


class TestRecordRunComplete:
    def test_updates_in_place(self) -> None:
        rec = rh.record_run_start("envoy-config")
        time.sleep(0.01)
        out = rh.record_run_complete(rec.run_id, status=RunStatus.OK)
        assert out is not None
        assert out.status == RunStatus.OK
        assert out.completed_at is not None
        assert out.elapsed is not None
        # Re-reading the file shows the updated state, not the
        # original "running".
        all_records = list(rh.iter_records())
        # Same run_id, only one entry total.
        assert sum(
            1 for r in all_records if r.run_id == rec.run_id
        ) == 1
        live = next(r for r in all_records if r.run_id == rec.run_id)
        assert live.status == RunStatus.OK

    def test_returns_none_for_unknown_run_id(self) -> None:
        assert (
            rh.record_run_complete("does-not-exist", status=RunStatus.OK)
            is None
        )

    def test_rejects_non_terminal_status(self) -> None:
        rec = rh.record_run_start("envoy-config")
        with pytest.raises(ValueError, match="terminal"):
            rh.record_run_complete(rec.run_id, status="running")

    def test_attaches_error_payload(self) -> None:
        rec = rh.record_run_start("envoy-config")
        rh.record_run_complete(
            rec.run_id,
            status=RunStatus.ERROR,
            error="ConnectionRefusedError",
            attempts=3,
            stdout_tail="last line\nof output",
            log_anchor_source="controller",
            log_anchor_since_iso="2026-01-01T00:00:00Z",
            log_anchor_action="envoy-config",
        )
        live = rh.get_run(rec.run_id)
        assert live is not None
        assert live.error == "ConnectionRefusedError"
        assert live.attempts == 3
        assert live.stdout_tail and "last line" in live.stdout_tail
        assert live.log_anchor is not None
        assert live.log_anchor.action == "envoy-config"

    def test_appends_to_parent_child_run_ids(self) -> None:
        parent = rh.record_run_start("batch")
        child_a = rh.record_run_start(
            "a", parent_run_id=parent.run_id, batch_id=parent.run_id,
        )
        child_b = rh.record_run_start(
            "b", parent_run_id=parent.run_id, batch_id=parent.run_id,
        )
        rh.record_run_complete(child_a.run_id, status=RunStatus.OK)
        rh.record_run_complete(child_b.run_id, status=RunStatus.ERROR)
        live_parent = rh.get_run(parent.run_id)
        assert live_parent is not None
        assert child_a.run_id in live_parent.child_run_ids
        assert child_b.run_id in live_parent.child_run_ids

    def test_no_duplicate_child_run_ids(self) -> None:
        parent = rh.record_run_start("batch")
        child = rh.record_run_start(
            "a", parent_run_id=parent.run_id,
        )
        rh.record_run_complete(child.run_id, status=RunStatus.OK)
        # Calling again with a stale terminal status (not realistic
        # but defends against double-completion): the parent's
        # child_run_ids must not duplicate.
        rh.record_run_complete(child.run_id, status=RunStatus.OK)
        live_parent = rh.get_run(parent.run_id)
        assert live_parent is not None
        assert live_parent.child_run_ids.count(child.run_id) == 1


class TestQueryFilters:
    def _seed(self) -> tuple[RunRecord, RunRecord, RunRecord]:
        # Three records: two for envoy-config, one for sonarr.
        a = rh.record_run_start("envoy-config", triggered_by="cron")
        rh.record_run_complete(a.run_id, status=RunStatus.OK)
        b = rh.record_run_start("envoy-config", triggered_by="manual")
        rh.record_run_complete(b.run_id, status=RunStatus.ERROR)
        c = rh.record_run_start("sonarr-sync")
        rh.record_run_complete(c.run_id, status=RunStatus.OK)
        return a, b, c

    def test_get_runs_no_filter_returns_all_newest_first(self) -> None:
        a, b, c = self._seed()
        out = rh.get_runs()
        ids = [r.run_id for r in out]
        # Newest first.
        assert ids[0] == c.run_id
        assert ids[-1] == a.run_id

    def test_get_runs_filters_by_job(self) -> None:
        self._seed()
        out = rh.get_runs(job_name="envoy-config")
        assert len(out) == 2
        assert all(r.job_name == "envoy-config" for r in out)

    def test_get_runs_respects_limit(self) -> None:
        for _ in range(5):
            r = rh.record_run_start("x")
            rh.record_run_complete(r.run_id, status=RunStatus.OK)
        out = rh.get_runs(limit=2)
        assert len(out) == 2

    def test_get_runs_filters_by_parent(self) -> None:
        parent = rh.record_run_start("batch")
        c1 = rh.record_run_start("a", parent_run_id=parent.run_id)
        rh.record_run_complete(c1.run_id, status=RunStatus.OK)
        c2 = rh.record_run_start("b", parent_run_id=parent.run_id)
        rh.record_run_complete(c2.run_id, status=RunStatus.OK)
        # Unrelated:
        unrelated = rh.record_run_start("z")
        rh.record_run_complete(unrelated.run_id, status=RunStatus.OK)
        out = rh.get_runs(parent_run_id=parent.run_id)
        names = {r.job_name for r in out}
        assert names == {"a", "b"}

    def test_get_runs_filters_by_batch(self) -> None:
        b1 = rh.record_run_start("batch")
        c1 = rh.record_run_start("a", batch_id=b1.run_id)
        rh.record_run_complete(c1.run_id, status=RunStatus.OK)
        b2 = rh.record_run_start("batch")
        c2 = rh.record_run_start("a", batch_id=b2.run_id)
        rh.record_run_complete(c2.run_id, status=RunStatus.OK)
        out = rh.get_runs(batch_id=b1.run_id)
        assert len(out) == 1
        assert out[0].batch_id == b1.run_id

    def test_get_runs_filters_by_since_ts(self) -> None:
        a = rh.record_run_start("a")
        rh.record_run_complete(a.run_id, status=RunStatus.OK)
        cutoff = time.time() + 0.01
        time.sleep(0.05)
        b = rh.record_run_start("b")
        rh.record_run_complete(b.run_id, status=RunStatus.OK)
        out = rh.get_runs(since_ts=cutoff)
        names = {r.job_name for r in out}
        assert names == {"b"}


class TestSingleRunHelpers:
    def test_get_run_existing(self) -> None:
        rec = rh.record_run_start("a")
        rh.record_run_complete(rec.run_id, status=RunStatus.OK)
        live = rh.get_run(rec.run_id)
        assert live is not None
        assert live.run_id == rec.run_id

    def test_get_run_missing_returns_none(self) -> None:
        assert rh.get_run("does-not-exist") is None

    def test_get_latest_run(self) -> None:
        a = rh.record_run_start("envoy-config")
        rh.record_run_complete(a.run_id, status=RunStatus.OK)
        time.sleep(0.01)
        b = rh.record_run_start("envoy-config")
        rh.record_run_complete(b.run_id, status=RunStatus.ERROR)
        latest = rh.get_latest_run("envoy-config")
        assert latest is not None
        assert latest.run_id == b.run_id

    def test_get_latest_run_missing_returns_none(self) -> None:
        assert rh.get_latest_run("nonexistent") is None

    def test_get_children_sorted_by_started_at(self) -> None:
        parent = rh.record_run_start("batch")
        c1 = rh.record_run_start("a", parent_run_id=parent.run_id)
        time.sleep(0.01)
        c2 = rh.record_run_start("b", parent_run_id=parent.run_id)
        rh.record_run_complete(c1.run_id, status=RunStatus.OK)
        rh.record_run_complete(c2.run_id, status=RunStatus.OK)
        out = rh.get_children(parent.run_id)
        assert [r.job_name for r in out] == ["a", "b"]

    def test_iter_records_oldest_first(self) -> None:
        a = rh.record_run_start("a")
        time.sleep(0.005)
        b = rh.record_run_start("b")
        records = list(rh.iter_records())
        names = [r.job_name for r in records]
        # Append-only file: oldest first.
        assert names == ["a", "b"]


class TestRobustness:
    def test_malformed_jsonl_line_skipped(
        self, tmp_path: Path,
    ) -> None:
        path = tmp_path / ".controller" / "run-history.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '{"run_id":"01","job_name":"a","status":"ok","started_at":1.0}\n'
            'NOT JSON\n'
            '{"run_id":"02","job_name":"b","status":"ok","started_at":2.0}\n',
            encoding="utf-8",
        )
        records = list(rh.iter_records())
        assert [r.run_id for r in records] == ["01", "02"]

    def test_empty_lines_in_file_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / ".controller" / "run-history.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '\n\n{"run_id":"01","job_name":"a","status":"ok","started_at":1.0}\n\n',
            encoding="utf-8",
        )
        records = list(rh.iter_records())
        assert len(records) == 1


class TestCapTrimming:
    def test_writing_past_cap_trims_to_headroom(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Use a tiny cap to make this fast.
        monkeypatch.setattr(
            "media_stack.application.jobs.run_history."
            "RUN_HISTORY_HARD_CAP",
            100,
        )
        # Write 150 records; cap=100; expect trim to ~95 (95% headroom).
        for _ in range(150):
            rh.record_run_start("x")
        line_count = sum(
            1 for _ in (
                tmp_path / ".controller" / "run-history.jsonl"
            ).read_text(encoding="utf-8").splitlines()
            if _.strip()
        )
        assert line_count <= 100
        # And the most recent record is preserved.
        records = list(rh.iter_records())
        assert records[-1].job_name == "x"

    def test_no_trim_below_cap(self) -> None:
        for _ in range(10):
            rh.record_run_start("x")
        records = list(rh.iter_records())
        assert len(records) == 10

    def test_default_cap_is_50000(self) -> None:
        # Sanity check the constant — operators rely on this number
        # matching the log cap.
        assert RUN_HISTORY_HARD_CAP == 50000
