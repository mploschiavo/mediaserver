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

Promise-probe + ensurer pair, same shape as
``jellyfin:ensure-api-key``: the probe checks the invariant, the
ensurer makes it true, the auto-heal cycle re-evaluates every 60s.
Reuses ``run_history_repair`` which already had the logic factored
out as a pure function with ``apply``/``dry_run`` modes, atomic
rewrite, and idempotent behavior.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from media_stack.application.jobs import run_history_repair
from media_stack.application.jobs.framework import JobContext
from media_stack.application.jobs.run_history import (
    _STALE_RUNNING_THRESHOLD_SECONDS,
    count_stale_running,
    resolve_run_history_path,
)


logger = logging.getLogger(__name__)


class CloseStaleRunsHandler:
    """Job-framework handler for the ``jobs:close-stale-runs`` cycle.

    Invokes the run-history repair tool when the probe finds stale
    ``status=running`` records; returns ``{"skipped": "nothing_stale"}``
    on the steady-state happy path.
    """

    def close_stale_runs(self, _ctx: JobContext) -> dict[str, Any]:
        """One auto-heal cycle. Returns the framework-expected result
        dict:

          * ``skipped: nothing_stale`` when the probe is green (most
            ticks)
          * ``status: ok, closed: N`` when records were closed
          * Raises on a structural error (history file unreadable, etc.)
            so JobRunner records terminal ``error`` and the operator
            sees it in /api/runs.
        """
        # Dispatch through ``sys.modules[__name__]`` so tests that
        # ``mock.patch("…close_stale_runs.count_stale_running", …)``
        # intercept the call: the test patches the module-level name,
        # not the original at ``run_history.count_stale_running``.
        _mod = sys.modules[__name__]
        stale = _mod.count_stale_running(_STALE_RUNNING_THRESHOLD_SECONDS)
        if stale == 0:
            return {"skipped": "nothing_stale"}

        history_path = _mod.run_history_repair.resolve_history_path(
            str(_mod.resolve_run_history_path()),
        )
        report = _mod.run_history_repair.run_repair(
            history_path=history_path,
            apply=True,
            older_than_seconds=_STALE_RUNNING_THRESHOLD_SECONDS,
            mark_as=_mod.run_history_repair.STATUS_ERROR,
            scenarios=[_mod.run_history_repair.SCENARIO_FIX_STUCK_RUNNING],
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


_INSTANCE = CloseStaleRunsHandler()

# Module-level alias preserves the legacy public-import + contract-handler
# path (``…close_stale_runs:close_stale_runs``) which the contract YAML
# at ``contracts/services/guardrails.yaml`` registers and which tests
# import directly.
close_stale_runs = _INSTANCE.close_stale_runs
