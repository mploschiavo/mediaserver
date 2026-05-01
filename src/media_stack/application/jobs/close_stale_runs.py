"""Job handler: close run-history records stuck at status=running.

Promise-style ensurer that satisfies the
``run-history-no-stale-running`` invariant. Runs on the auto-heal
cycle every 60s; idempotent (returns ``skipped: nothing_stale``
when the probe is already green).

The threat model has two failure modes for zombie ``status=running``
records:

  1. Handler raises while the controller process is still alive.
     ``Job.run()`` already absorbs the exception and returns an
     error-shaped result, which JobRunner closes via
     ``record_run_complete``. NOT a vector — no fix needed.

  2. Controller process dies (SIGKILL, OOM, deploy-recreate)
     mid-handler, before ``record_run_complete`` runs. NO try/finally
     can save you here. Self-heals on the next auto-heal tick after
     restart via this handler.

Same Phase 0 pattern as ``jellyfin:ensure-api-key`` — promise probes
the invariant, ensurer makes it true, auto-heal re-evaluates every
60s. Same shape, no new code path. Reuses ``run_history_repair``
which already had the logic factored out as a pure function with
``apply``/``dry_run`` modes, atomic rewrite, and idempotent behavior.
"""

from __future__ import annotations

import logging
from typing import Any

from media_stack.application.jobs import run_history_repair
from media_stack.application.jobs.framework import JobContext
from media_stack.application.jobs.run_history import (
    _STALE_RUNNING_THRESHOLD_SECONDS,
    count_stale_running,
    resolve_run_history_path,
)


logger = logging.getLogger(__name__)


def close_stale_runs(_ctx: JobContext) -> dict[str, Any]:
    """One auto-heal cycle. Returns the framework-expected result
    dict:

      * ``skipped: nothing_stale`` when the probe is green (most ticks)
      * ``status: ok, closed: N`` when records were closed
      * Raises on a structural error (history file unreadable, etc.)
        so JobRunner records terminal ``error`` and the operator
        sees it in /api/runs.
    """
    stale = count_stale_running(_STALE_RUNNING_THRESHOLD_SECONDS)
    if stale == 0:
        return {"skipped": "nothing_stale"}

    history_path = run_history_repair.resolve_history_path(
        str(resolve_run_history_path()),
    )
    report = run_history_repair.run_repair(
        history_path=history_path,
        apply=True,
        older_than_seconds=_STALE_RUNNING_THRESHOLD_SECONDS,
        mark_as=run_history_repair.STATUS_ERROR,
        scenarios=[run_history_repair.SCENARIO_FIX_STUCK_RUNNING],
        backup=False,
    )
    closed = len(report.actions)
    if closed:
        logger.info(
            "[close_stale_runs] closed %d stale running record(s); "
            "first run_id=%s",
            closed,
            report.actions[0].run_id if report.actions else "",
        )
    return {
        "status": "ok",
        "closed": closed,
        "stale_observed": stale,
    }
