"""Regression test: JobRunner closes run records on handler errors.

Pins the invariant that, given a synchronous job whose handler
raises, the run-history record is still rewritten with
``status=error`` (not left at ``status=running``).

Important nuance: this is NOT a try/finally we added in v1.0.293.
The framework's ``Job.run()`` already absorbs handler exceptions
internally and returns ``{"status": "error", "error": "..."}``.
JobRunner sees the error-shaped result, not the exception, and
calls ``record_run_complete`` with that terminal status. The test
locks in this property so a future refactor that "improves" error
propagation by removing the inner try/except would fail CI.

The remaining zombie-records vector — controller process death
(SIGKILL, OOM, deploy-recreate) before ``record_run_complete``
gets called — is structurally unfixable in code at the
JobRunner layer. It's handled instead by the
``jobs:close-stale-runs`` ensurer that runs on the auto-heal
cycle (Phase 0 of ADR-0003).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


def _make_synchronous_job(name: str, raise_with: Exception | None = None):
    """Build a minimal leaf Job. Job's design accommodates
    ``requires`` (named prereqs), ``after`` (job-name ordering),
    and n-level ``sub_jobs``; none of those are exercised here."""
    from media_stack.domain.jobs.types import Job

    if raise_with is not None:
        def handler(_ctx):
            raise raise_with
    else:
        def handler(_ctx):
            return {"status": "ok"}

    return Job(name=name, handler=handler, requires=[], after=[])


class TestJobRunnerClosesRunRecordOnHandlerException:
    """The invariant: a raising handler must NOT leave a
    ``status=running`` record behind. ``Job.run()`` absorbs the
    exception; JobRunner records the run with terminal
    ``status=error``."""

    def test_handler_exception_still_closes_run_record_with_error_status(
        self,
    ) -> None:
        from media_stack.application.jobs.framework import (
            JobContext,
            JobRunner,
        )
        from media_stack.domain.jobs.run_record import RunStatus

        complete_calls: list[dict] = []
        start_calls: list[str] = []

        def fake_complete(run_id, **kwargs):
            complete_calls.append({"run_id": run_id, **kwargs})
            return None

        class _StubRecord:
            def __init__(self, run_id: str) -> None:
                self.run_id = run_id

        def fake_start(name, **_kw):
            run_id = f"stub-{name}"
            start_calls.append(run_id)
            return _StubRecord(run_id)

        boom = RuntimeError("handler exploded")
        job = _make_synchronous_job("test:explodes", raise_with=boom)
        ctx = JobContext()
        runner = JobRunner(root=job, ctx=ctx, source="test")

        with patch(
            "media_stack.application.jobs.run_history.record_run_start",
            side_effect=fake_start,
        ), patch(
            "media_stack.application.jobs.run_history.record_run_complete",
            side_effect=fake_complete,
        ):
            # No exception expected — Job.run() absorbs it. The
            # whole point of this test is to lock in that contract.
            result = runner.run()

        # Job.run() ate the exception and returned an error result.
        assert result["status"] == "error", (
            f"expected JobRunner.run() to return status=error after "
            f"a handler exception; got {result}"
        )

        # Per-job run record was started AND closed with status=error.
        assert "stub-test:explodes" in start_calls, (
            f"expected start record for test:explodes; got {start_calls}"
        )
        per_job_complete = [
            c for c in complete_calls if c["run_id"] == "stub-test:explodes"
        ]
        assert per_job_complete, (
            f"expected record_run_complete to be called for the failing "
            f"job; complete_calls={complete_calls}"
        )
        assert per_job_complete[0]["status"] == RunStatus.ERROR, (
            f"expected status=error after handler exception; "
            f"got status={per_job_complete[0]['status']!r}"
        )
        assert "handler exploded" in (per_job_complete[0].get("error") or ""), (
            f"expected exception message in run record; "
            f"got error={per_job_complete[0].get('error')!r}"
        )
