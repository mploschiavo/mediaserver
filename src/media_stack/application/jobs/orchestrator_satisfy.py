"""Job handler: orchestrator:satisfy-shadow — ADR-0003 Phase 4c.

Wraps ``application/services/orchestrator.py::satisfy_promises`` so
the existing auto-heal cycle can call it like any other Phase 0
ensurer. Runs in **dry-run mode** during shadow (Phase 4c-d) — probes
fire, but ensurers do NOT, so the orchestrator can't conflict with
the legacy bootstrap pipeline.

Phase 5 will flip this to ``dry_run=False`` (orchestrator becomes
primary; ensurers run; legacy paths get retired). Phase 4d uses the
shadow-mode tick records to compare orchestrator vs legacy outcomes
and chase discrepancies BEFORE flipping the switch.

The handler emits exactly ONE ``RunRecord`` per tick (via JobRunner's
normal lifecycle — ``run_job("orchestrator:satisfy-shadow")``). Per-
promise outcomes live in the cooldown state file (queryable for
"current state of promise X") and in the orchestrator's INFO logs
(grep-able for "tick history"). No 50+ records-per-minute spam.

Returning a result dict with summary fields keeps the run-history
search rich without bloat: ``status=ok`` plus ``ok_count``,
``failed_transient_count``, etc., all surface in the existing
/api/jobs/history payload.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from media_stack.application.jobs.framework import JobContext


logger = logging.getLogger(__name__)


def satisfy_shadow(_ctx: JobContext) -> dict[str, Any]:
    """One ``satisfy_promises`` tick in dry-run shadow mode.

    Returns the framework-expected result dict. JobRunner records
    a terminal status from ``status``/``skipped``; the summary fields
    end up in the run-history record so operators can chart "ok vs
    failed over time" without parsing logs.
    """
    from media_stack.application.services.orchestrator import (
        satisfy_promises,
    )

    platform = _detect_platform()
    summary = satisfy_promises(
        platform=platform,
        dry_run=True,
        history_emit=_no_op_emit,
    )

    if summary.has_failures:
        logger.info(
            "[orchestrator:satisfy-shadow] %s (%.2fs); platform=%s",
            summary.summary_line(), summary.elapsed_seconds, platform,
        )
    else:
        logger.debug(
            "[orchestrator:satisfy-shadow] %s (%.2fs); platform=%s",
            summary.summary_line(), summary.elapsed_seconds, platform,
        )

    return {
        "status": "ok",
        "platform": platform,
        "elapsed": round(summary.elapsed_seconds, 3),
        "total": summary.total,
        "ok_count": summary.ok,
        "failed_transient_count": summary.failed_transient,
        "failed_permanent_count": summary.failed_permanent,
        "dep_failed_count": summary.dep_failed,
        "skipped_cooldown_count": summary.skipped_cooldown,
        "skipped_platform_count": summary.skipped_platform,
        "unknown_count": summary.unknown,
    }


def _detect_platform() -> str:
    """``compose`` | ``k8s``. K8s exposes ``KUBERNETES_SERVICE_HOST``
    in every pod automatically; compose doesn't. ``MEDIA_STACK_RUNTIME``
    is an explicit override the deployer can set when the heuristic
    is wrong."""
    explicit = (os.environ.get("MEDIA_STACK_RUNTIME") or "").strip().lower()
    if explicit in ("compose", "k8s"):
        return explicit
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        return "k8s"
    return "compose"


def _no_op_emit(promise, attempt, phase):  # noqa: ANN001
    """Discard per-promise records during shadow mode. The cooldown
    state file holds the current state; the tick-level record from
    JobRunner holds the aggregate. Phase 5 may swap this for a real
    emitter once we know operators want per-promise queryability."""
    return None
