"""Unit tests for ``_build_per_job_history_record`` — the Jobs Phase 1
addition that surfaces ``error`` / ``skip_reason`` / ``attempts`` in
the per-job history payload so the dashboard can show "what failed?"
without ssh.
"""

from __future__ import annotations

from media_stack.application.jobs.framework import (
    _build_per_job_history_record,
)


class TestBuildPerJobHistoryRecord:
    def test_minimal_ok_run(self) -> None:
        out = _build_per_job_history_record({"status": "ok", "elapsed": 1.5})
        # Pre-v1.0.270 contract — preserved.
        assert out == {"status": "ok", "elapsed": 1.5}

    def test_error_text_surfaces(self) -> None:
        out = _build_per_job_history_record({
            "status": "error",
            "elapsed": 0.5,
            "error": "ConnectionRefusedError: [Errno 111] connection refused",
        })
        assert out["status"] == "error"
        assert (
            out["error"]
            == "ConnectionRefusedError: [Errno 111] connection refused"
        )

    def test_error_truncates_at_500(self) -> None:
        long_err = "x" * 1000
        out = _build_per_job_history_record({
            "status": "error",
            "elapsed": 0,
            "error": long_err,
        })
        assert len(out["error"]) == 500

    def test_skip_reason_surfaces(self) -> None:
        out = _build_per_job_history_record({
            "status": "skipped",
            "elapsed": 0,
            "skip_reason": "prereq configure-auth failed",
        })
        assert out["skip_reason"] == "prereq configure-auth failed"

    def test_skip_reason_alt_key(self) -> None:
        # Some legacy callers emit ``skipped_reason``.
        out = _build_per_job_history_record({
            "status": "skipped",
            "elapsed": 0,
            "skipped_reason": "auth not configured yet",
        })
        assert out["skip_reason"] == "auth not configured yet"

    def test_attempts_only_when_greater_than_one(self) -> None:
        out_one = _build_per_job_history_record({
            "status": "ok",
            "elapsed": 1,
            "attempts": 1,
        })
        assert "attempts" not in out_one
        out_three = _build_per_job_history_record({
            "status": "ok",
            "elapsed": 5,
            "attempts": 3,
        })
        assert out_three["attempts"] == 3

    def test_no_optional_fields_omitted(self) -> None:
        out = _build_per_job_history_record({"status": "ok", "elapsed": 0.1})
        assert "error" not in out
        assert "skip_reason" not in out
        assert "attempts" not in out

    def test_status_default_when_missing(self) -> None:
        out = _build_per_job_history_record({})
        assert out == {"status": "?", "elapsed": 0}
