"""Smoke tests for the operator CLI ``OrchestratorEvalCommand``.

The class is the testable surface; the module-level ``main`` is a
thin entrypoint. Tests inject a fake ``tick_callable`` + a captured
output stream so we never touch the real orchestrator or
``stdout``.
"""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

from media_stack.cli.commands.orchestrator_eval_main import (
    OrchestratorEvalCommand,
)
from media_stack.domain.services.promises import PromiseAttempt, TickSummary


def _summary(*, has_failures: bool, attempts: list[PromiseAttempt]) -> TickSummary:
    counts = {
        "ok": sum(1 for a in attempts if a.status == "ok"),
        "failed_transient": sum(
            1 for a in attempts if a.status == "failed_transient"
        ),
        "failed_permanent": sum(
            1 for a in attempts if a.status == "failed_permanent"
        ),
    }
    summary = TickSummary(
        started_at=0.0, elapsed_seconds=0.5,
        total=len(attempts),
        ok=counts["ok"],
        failed_transient=counts["failed_transient"],
        failed_permanent=counts["failed_permanent"],
        dep_failed=0, skipped_cooldown=0, skipped_platform=0, unknown=0,
        attempts=tuple(attempts),
    )
    # Sanity: caller and dataclass agree about the failure shape.
    assert summary.has_failures is has_failures
    return summary


def _attempt(pid: str, status: str) -> PromiseAttempt:
    return PromiseAttempt(
        promise_id=pid,
        status=status,  # type: ignore[arg-type]
        started_at=0.0,
        elapsed_seconds=0.012,
        detail="responsive",
    )


class TestExitCode:
    def test_returns_0_when_no_failures(self) -> None:
        captured: dict[str, Any] = {}

        def _tick(**kwargs: Any) -> TickSummary:
            captured.update(kwargs)
            return _summary(
                has_failures=False,
                attempts=[_attempt("a", "ok"), _attempt("b", "ok")],
            )

        out = io.StringIO()
        cmd = OrchestratorEvalCommand(
            out=out, err=io.StringIO(),
            tick_callable=_tick,
            configure_logging=False,
        )
        rc = cmd.run([])
        assert rc == 0
        assert captured["dry_run"] is True  # default
        assert captured["platform"] == "compose"

    def test_returns_1_when_summary_has_failures(self) -> None:
        def _tick(**_kwargs: Any) -> TickSummary:
            return _summary(
                has_failures=True,
                attempts=[
                    _attempt("a", "ok"),
                    _attempt("b", "failed_transient"),
                ],
            )

        cmd = OrchestratorEvalCommand(
            out=io.StringIO(), err=io.StringIO(),
            tick_callable=_tick,
            configure_logging=False,
        )
        assert cmd.run([]) == 1


class TestArgparse:
    def test_apply_flag_disables_dry_run(self) -> None:
        captured: dict[str, Any] = {}

        def _tick(**kwargs: Any) -> TickSummary:
            captured.update(kwargs)
            return _summary(has_failures=False, attempts=[])

        cmd = OrchestratorEvalCommand(
            out=io.StringIO(), err=io.StringIO(),
            tick_callable=_tick,
            configure_logging=False,
        )
        cmd.run(["--apply"])
        assert captured["dry_run"] is False

    def test_platform_arg_propagates(self) -> None:
        captured: dict[str, Any] = {}

        def _tick(**kwargs: Any) -> TickSummary:
            captured.update(kwargs)
            return _summary(has_failures=False, attempts=[])

        cmd = OrchestratorEvalCommand(
            out=io.StringIO(), err=io.StringIO(),
            tick_callable=_tick,
            configure_logging=False,
        )
        cmd.run(["--platform", "k8s"])
        assert captured["platform"] == "k8s"

    def test_workers_arg_propagates(self) -> None:
        captured: dict[str, Any] = {}

        def _tick(**kwargs: Any) -> TickSummary:
            captured.update(kwargs)
            return _summary(has_failures=False, attempts=[])

        cmd = OrchestratorEvalCommand(
            out=io.StringIO(), err=io.StringIO(),
            tick_callable=_tick,
            configure_logging=False,
        )
        cmd.run(["--workers", "16"])
        assert captured["workers"] == 16


class TestOutput:
    def test_table_output_lists_every_attempt(self) -> None:
        out = io.StringIO()
        attempts = [
            _attempt("a-promise", "ok"),
            _attempt("b-promise", "failed_transient"),
        ]
        cmd = OrchestratorEvalCommand(
            out=out, err=io.StringIO(),
            tick_callable=lambda **_kw: _summary(
                has_failures=True, attempts=attempts,
            ),
            configure_logging=False,
        )
        cmd.run([])
        rendered = out.getvalue()
        assert "a-promise" in rendered
        assert "b-promise" in rendered
        assert "PROMISE" in rendered  # header
        assert "summary:" in rendered

    def test_json_output_one_object_per_attempt_plus_summary(self) -> None:
        out = io.StringIO()
        cmd = OrchestratorEvalCommand(
            out=out, err=io.StringIO(),
            tick_callable=lambda **_kw: _summary(
                has_failures=False,
                attempts=[_attempt("a", "ok"), _attempt("b", "ok")],
            ),
            configure_logging=False,
        )
        cmd.run(["--json"])
        lines = [ln for ln in out.getvalue().splitlines() if ln.strip()]
        # 2 attempt lines + 1 summary line
        assert len(lines) == 3
        # Last line is the summary envelope.
        last = json.loads(lines[-1])
        assert "summary" in last
        assert last["summary"]["total"] == 2
        assert last["summary"]["ok"] == 2

    def test_table_groups_ok_separately_from_failures(self) -> None:
        # Sort key is ``(status != "ok", promise_id)`` so all ok
        # rows print before any non-ok row, then within each group
        # rows are alphabetised. The intent: an operator scanning
        # the output sees the green block first, then the failures
        # cluster at the bottom where they're easy to grep.
        out = io.StringIO()
        attempts = [
            _attempt("aaa-fail", "failed_transient"),
            _attempt("zzz-ok", "ok"),
            _attempt("mmm-ok", "ok"),
        ]
        cmd = OrchestratorEvalCommand(
            out=out, err=io.StringIO(),
            tick_callable=lambda **_kw: _summary(
                has_failures=True, attempts=attempts,
            ),
            configure_logging=False,
        )
        cmd.run([])
        rendered = out.getvalue()
        body = rendered.split("\n", 2)[2]  # skip header + separator
        # Both ok rows appear before the failure row.
        assert body.find("mmm-ok") < body.find("aaa-fail")
        assert body.find("zzz-ok") < body.find("aaa-fail")
        # Within the ok group, rows are alphabetised.
        assert body.find("mmm-ok") < body.find("zzz-ok")
