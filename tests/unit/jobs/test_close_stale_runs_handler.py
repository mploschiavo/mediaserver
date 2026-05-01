"""Tests for ``application.jobs.close_stale_runs``.

The handler closes any run-history records stuck at
``status=running`` for >5 min. Three branches:

  1. Probe is green (no stale records) → returns
     ``skipped: nothing_stale`` in <10ms. The auto-heal cycle's
     happy path; runs every 60s on every controller.
  2. Probe finds N stale records → calls
     ``run_history_repair.run_repair`` with ``apply=True``,
     reports ``status=ok, closed=N``.
  3. The repair logic itself raises (e.g. the history file is
     unreadable) → handler raises so JobRunner records terminal
     ``error`` and the operator sees the failure in /api/runs.

Same Phase 0 promise-style pattern as ``jellyfin:ensure-api-key``:
probe-then-ensure on the auto-heal cycle, idempotent, no boot-time
hooks. Logic was moved from ``bin/ops/repair_run_history.py`` to
``media_stack.application.jobs.run_history_repair`` in v1.0.293
so this handler can ``import`` it normally instead of doing an
importlib spec dance to find a script outside the package.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class _StubCtx:
    """JobContext stand-in. The handler doesn't read from ctx."""


def test_skips_when_no_stale_records() -> None:
    """The probe-is-green branch — most common case in steady state.
    The repair logic MUST NOT be invoked when there's nothing to do."""
    from media_stack.application.jobs.close_stale_runs import (
        close_stale_runs,
    )

    with patch(
        "media_stack.application.jobs.close_stale_runs.count_stale_running",
        return_value=0,
    ), patch(
        "media_stack.application.jobs.close_stale_runs.run_history_repair.run_repair",
    ) as mock_run_repair:
        result = close_stale_runs(_StubCtx())
    assert result["skipped"] == "nothing_stale"
    # Steady-state cost: zero calls into the repair tool.
    mock_run_repair.assert_not_called()


def test_closes_stale_records_when_probe_finds_them() -> None:
    """The probe-finds-stale branch — drives the repair tool."""
    from media_stack.application.jobs.close_stale_runs import (
        close_stale_runs,
    )

    fake_report = MagicMock()
    fake_report.actions = [
        MagicMock(run_id="01ABCDE"),
        MagicMock(run_id="01FGHIJ"),
    ]

    with patch(
        "media_stack.application.jobs.close_stale_runs.count_stale_running",
        return_value=2,
    ), patch(
        "media_stack.application.jobs.close_stale_runs.run_history_repair.run_repair",
        return_value=fake_report,
    ) as mock_run_repair, patch(
        "media_stack.application.jobs.close_stale_runs.run_history_repair.resolve_history_path",
        return_value="/srv-config/.controller/run-history.jsonl",
    ):
        result = close_stale_runs(_StubCtx())

    assert result["status"] == "ok"
    assert result["closed"] == 2
    assert result["stale_observed"] == 2
    mock_run_repair.assert_called_once()
    # Verify apply=True is wired correctly. Without it the repair
    # tool runs in dry-run mode and zombies persist.
    kwargs = mock_run_repair.call_args.kwargs
    assert kwargs["apply"] is True
    assert kwargs["scenarios"] == ["fix-stuck-running"]
    assert kwargs["mark_as"] == "error"


def test_raises_when_repair_logic_raises() -> None:
    """If the underlying repair logic raises (history file
    unreadable, atomic-rename collision, etc.), the handler MUST
    propagate so JobRunner records terminal ``error`` and the
    operator sees it. Silent failures here would leave zombies
    accumulating without any signal."""
    from media_stack.application.jobs.close_stale_runs import (
        close_stale_runs,
    )

    with patch(
        "media_stack.application.jobs.close_stale_runs.count_stale_running",
        return_value=3,
    ), patch(
        "media_stack.application.jobs.close_stale_runs.run_history_repair.run_repair",
        side_effect=RuntimeError("history file is corrupt"),
    ), patch(
        "media_stack.application.jobs.close_stale_runs.run_history_repair.resolve_history_path",
        return_value="/srv-config/.controller/run-history.jsonl",
    ):
        with pytest.raises(RuntimeError, match="corrupt"):
            close_stale_runs(_StubCtx())


def test_handler_is_registered_in_guardrails_yaml() -> None:
    """Contract-level smoke: the job MUST be registered under
    ``plugin.jobs`` in the guardrails service contract, otherwise
    the auto-heal hook in ``api/services/auto_heal.py`` will fail
    silently with 'unknown job' on every cycle."""
    from pathlib import Path

    import yaml

    contract = (
        Path(__file__).resolve().parents[3]
        / "contracts" / "services" / "guardrails.yaml"
    )
    data = yaml.safe_load(contract.read_text(encoding="utf-8"))
    jobs = data.get("plugin", {}).get("jobs", {}) or {}
    assert "jobs:close-stale-runs" in jobs, (
        "jobs:close-stale-runs not registered in contracts/services/"
        "guardrails.yaml::plugin.jobs. The auto-heal cycle's "
        "run_job() call would fail with 'unknown job' on every tick."
    )
    handler_path = jobs["jobs:close-stale-runs"].get("handler", "")
    assert handler_path == (
        "media_stack.application.jobs.close_stale_runs:close_stale_runs"
    ), (
        f"unexpected handler path; got {handler_path!r}. "
        "If the module moved, update the contract together with "
        "the move (see ADR-0002 cleanup tail)."
    )
