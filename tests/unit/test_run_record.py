"""Unit tests for ``domain.jobs.run_record``.

Covers:
  * ULID generation: shape, monotonicity per millisecond, uniqueness
    across many calls
  * RunStatus.TERMINAL membership
  * RunRecord round-trip (to_dict / from_dict)
  * Truncation invariants (stdout_tail, error)
  * LogAnchor optional-field handling
  * resolve_run_history_path env override
"""

from __future__ import annotations

import os
import re
import time

import pytest

from media_stack.domain.jobs.run_record import (
    RUN_HISTORY_HARD_CAP,
    RUN_STDOUT_TAIL_CAP,
    LogAnchor,
    RunRecord,
    RunStatus,
    make_run_id,
    resolve_run_history_path,
    truncate_stdout_tail,
)


class TestMakeRunId:
    def test_shape(self) -> None:
        rid = make_run_id()
        # 26-char Crockford Base32 — uppercase letters + digits,
        # excluding I/L/O/U.
        assert isinstance(rid, str)
        assert len(rid) == 26
        assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]+", rid)

    def test_unique_across_many_calls(self) -> None:
        ids = {make_run_id() for _ in range(2000)}
        # Birthday-paradox margin: 80 bits of randomness × 2000
        # calls is essentially guaranteed-unique.
        assert len(ids) == 2000

    def test_sortable_by_time(self) -> None:
        # Force two distinct timestamps; the second must sort after
        # the first.
        a = make_run_id(now_ms=1700_000_000_000)
        b = make_run_id(now_ms=1700_000_000_500)
        assert a < b

    def test_now_ms_default_uses_clock(self) -> None:
        before = int(time.time() * 1000)
        rid = make_run_id()
        after = int(time.time() * 1000)
        # Decode the timestamp prefix (first 10 chars, base32 →
        # 48-bit integer).
        alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
        ts = 0
        for ch in rid[:10]:
            ts = (ts << 5) | alphabet.index(ch)
        assert before <= ts <= after


class TestRunStatus:
    def test_terminal_membership(self) -> None:
        assert "ok" in RunStatus.TERMINAL
        assert "skipped" in RunStatus.TERMINAL
        assert "error" in RunStatus.TERMINAL
        assert "cancelled" in RunStatus.TERMINAL
        assert "timeout" in RunStatus.TERMINAL
        # Running is NOT terminal — that's the whole point.
        assert "running" not in RunStatus.TERMINAL


class TestLogAnchor:
    def test_round_trip_minimal(self) -> None:
        a = LogAnchor(source="controller", since_iso="2026-01-01T00:00:00Z")
        d = a.to_dict()
        assert d == {
            "source": "controller",
            "since_iso": "2026-01-01T00:00:00Z",
        }

    def test_round_trip_full(self) -> None:
        a = LogAnchor(
            source="controller",
            since_iso="2026-01-01T00:00:00Z",
            until_iso="2026-01-01T00:01:00Z",
            action="envoy-config",
        )
        d = a.to_dict()
        assert d["until_iso"] == "2026-01-01T00:01:00Z"
        assert d["action"] == "envoy-config"


class TestRunRecord:
    def _minimal(self) -> RunRecord:
        return RunRecord(
            run_id=make_run_id(),
            job_name="envoy-config",
            status=RunStatus.OK,
            started_at=1700_000_000.0,
        )

    def test_round_trip(self) -> None:
        r = self._minimal()
        r.parent_run_id = make_run_id()
        r.batch_id = make_run_id()
        r.completed_at = 1700_000_005.5
        r.elapsed = 5.5
        r.triggered_by = "cron"
        r.actor = "alice"
        r.attempts = 2
        r.error = "boom"
        r.stdout_tail = "tail data"
        r.log_anchor = LogAnchor(
            source="controller",
            since_iso="2026-01-01T00:00:00Z",
            action="envoy-config",
        )
        r.child_run_ids = [make_run_id(), make_run_id()]
        d = r.to_dict()
        r2 = RunRecord.from_dict(d)
        assert r2.run_id == r.run_id
        assert r2.job_name == r.job_name
        assert r2.parent_run_id == r.parent_run_id
        assert r2.elapsed == r.elapsed
        assert r2.error == r.error
        assert r2.attempts == 2
        assert r2.log_anchor is not None
        assert r2.log_anchor.action == "envoy-config"
        assert r2.child_run_ids == r.child_run_ids

    def test_minimal_serialization_omits_none(self) -> None:
        r = self._minimal()
        d = r.to_dict()
        # Optional fields should NOT appear when unset.
        for key in (
            "parent_run_id",
            "batch_id",
            "completed_at",
            "elapsed",
            "actor",
            "error",
            "stdout_tail",
            "log_anchor",
        ):
            assert key not in d
        # Required fields always present.
        for key in (
            "run_id", "job_name", "status", "started_at",
            "triggered_by", "attempts", "child_run_ids",
        ):
            assert key in d

    def test_stdout_tail_truncated_on_construct(self) -> None:
        big = "x" * (RUN_STDOUT_TAIL_CAP * 3)
        r = RunRecord(
            run_id=make_run_id(),
            job_name="x",
            status=RunStatus.OK,
            started_at=1.0,
            stdout_tail=big,
        )
        assert r.stdout_tail is not None
        assert len(r.stdout_tail) == RUN_STDOUT_TAIL_CAP
        # Tail-end is preserved (the operator wants the LAST screen
        # of output, not the first).
        assert r.stdout_tail.endswith("x" * 100)

    def test_error_truncated_on_construct(self) -> None:
        big = "y" * 1000
        r = RunRecord(
            run_id=make_run_id(),
            job_name="x",
            status=RunStatus.ERROR,
            started_at=1.0,
            error=big,
        )
        assert r.error is not None
        assert len(r.error) == 500

    def test_from_dict_handles_missing_fields(self) -> None:
        # A legacy / corrupted record with only the minimum.
        r = RunRecord.from_dict({
            "run_id": "01J5",
            "job_name": "x",
            "status": "ok",
            "started_at": 1.0,
        })
        assert r.run_id == "01J5"
        assert r.attempts == 1
        assert r.child_run_ids == []
        assert r.log_anchor is None

    def test_from_dict_loads_log_anchor(self) -> None:
        r = RunRecord.from_dict({
            "run_id": "01J5",
            "job_name": "x",
            "status": "ok",
            "started_at": 1.0,
            "log_anchor": {
                "source": "controller",
                "since_iso": "2026-01-01T00:00:00Z",
                "action": "x",
            },
        })
        assert r.log_anchor is not None
        assert r.log_anchor.source == "controller"
        assert r.log_anchor.action == "x"


class TestTruncateStdoutTail:
    def test_short_passthrough(self) -> None:
        assert truncate_stdout_tail("hello") == "hello"

    def test_empty_input(self) -> None:
        assert truncate_stdout_tail("") == ""

    def test_long_truncated_tail_preserved(self) -> None:
        big = "x" * (RUN_STDOUT_TAIL_CAP * 2)
        out = truncate_stdout_tail(big)
        assert len(out) == RUN_STDOUT_TAIL_CAP

    def test_at_cap_no_change(self) -> None:
        exact = "y" * RUN_STDOUT_TAIL_CAP
        assert truncate_stdout_tail(exact) == exact


class TestResolveRunHistoryPath:
    def test_uses_config_root_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path,
    ) -> None:
        monkeypatch.setenv("CONFIG_ROOT", str(tmp_path))
        from pathlib import Path
        path = Path(resolve_run_history_path())
        assert path.parent == tmp_path / ".controller"
        assert path.name == "run-history.jsonl"

    def test_default_when_env_missing(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("CONFIG_ROOT", raising=False)
        from pathlib import Path
        path = Path(resolve_run_history_path())
        assert str(path).startswith("/srv-config/")
        assert path.name == "run-history.jsonl"


class TestHardCaps:
    def test_run_history_cap_matches_log_cap_exactly(self) -> None:
        # Operators read run history at the same scale they read
        # logs — the two caps must agree so neither side becomes the
        # limiting factor.
        from media_stack.api.services.ops import LOG_LINES_HARD_CAP
        assert RUN_HISTORY_HARD_CAP == LOG_LINES_HARD_CAP
