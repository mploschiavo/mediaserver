"""Job handler: orchestrator:satisfy-shadow.

Wraps ``application/services/orchestrator.py::satisfy_promises`` so
the auto-heal cycle invokes it once per tick. The job itself always
runs ``dry_run=True``; per-service "is this service primary or
shadow?" is decided by ``ORCHESTRATOR_LIVE_SERVICES`` (see helper
below) which the dispatcher consults before running an ensurer.

The handler emits exactly ONE ``RunRecord`` per tick (via JobRunner's
normal lifecycle). Per-promise outcomes live in the cooldown state
file (queryable for "current state of promise X") and in the
orchestrator's INFO logs (grep-able for "tick history"). No 50+
records-per-minute spam.

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
    """One ``satisfy_promises`` tick.

    Returns the framework-expected result dict. JobRunner records
    a terminal status from ``status``/``skipped``; the summary fields
    end up in the run-history record so operators can chart "ok vs
    failed over time" without parsing logs.

    Staged-rollout knob: ``ORCHESTRATOR_LIVE_SERVICES`` env is a
    comma-separated list of service ids whose ensurers should run
    for real. Empty/unset = full dry-run shadow (probes fire, no
    ensurer side-effects). Operators can flip this without
    rebuilding the image — intentional, so a regression can revert
    with a single env-var change.
    """
    from media_stack.application.services.orchestrator import (
        satisfy_promises,
    )

    platform = _detect_platform()
    live_services = _live_services_from_env()
    summary = satisfy_promises(
        platform=platform,
        dry_run=True,
        live_services=live_services,
        history_emit=_no_op_emit,
    )

    live_services_str = ",".join(sorted(live_services)) if live_services else ""
    if summary.has_failures:
        logger.info(
            "[orchestrator:satisfy-shadow] %s (%.2fs); platform=%s; live=%s",
            summary.summary_line(), summary.elapsed_seconds, platform,
            live_services_str or "(none)",
        )
    else:
        logger.debug(
            "[orchestrator:satisfy-shadow] %s (%.2fs); platform=%s; live=%s",
            summary.summary_line(), summary.elapsed_seconds, platform,
            live_services_str or "(none)",
        )

    return {
        "status": "ok",
        "platform": platform,
        "elapsed": round(summary.elapsed_seconds, 3),
        "live_services": live_services_str,
        "total": summary.total,
        "ok_count": summary.ok,
        "failed_transient_count": summary.failed_transient,
        "failed_permanent_count": summary.failed_permanent,
        "dep_failed_count": summary.dep_failed,
        "skipped_cooldown_count": summary.skipped_cooldown,
        "skipped_platform_count": summary.skipped_platform,
        "unknown_count": summary.unknown,
    }


def _live_services_from_env() -> "frozenset[str] | None":
    """Read ``ORCHESTRATOR_LIVE_SERVICES`` env (comma-separated). Empty
    or unset → ``None`` (full dry-run shadow — probes fire, no
    ensurers run).
    """
    raw = (os.environ.get("ORCHESTRATOR_LIVE_SERVICES") or "").strip()
    if not raw:
        return None
    parts = frozenset(s.strip().lower() for s in raw.split(",") if s.strip())
    return parts or None


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
    JobRunner holds the aggregate. Could be swapped for a real
    emitter if operators want per-promise queryability."""
    return None
