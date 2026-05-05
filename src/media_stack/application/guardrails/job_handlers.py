"""Job-framework handlers for the guardrails subsystem.

The handler is a ``Callable[[JobContext], dict[str, Any]]`` so
the ``Job`` framework in ``media_stack.application.jobs.framework``
can invoke it through ``run_job("guardrails:evaluate", ...)``.

History recording is delegated to ``run_job`` itself (which calls
``record_run_start`` / ``record_run_complete``); the inner
``tick()`` is invoked with ``record_history=False`` so the legacy
``_record_history`` path doesn't double-write into the older
``/api/jobs.history[]`` aggregator. After this commit, every
guardrails cycle shows up in ``GET /api/runs`` exactly like every
other job — same shape, same RunDrawer, same ``Recent runs``
column treatment.

Throttling stays inside ``tick()`` (driven by
``MEDIA_STACK_GUARDRAIL_INTERVAL_SECONDS``, default 300s). When
the throttle's active, the handler returns
``{"skipped": "throttled", ...}`` and the framework records the
run with status ``skipped`` — which is positive evidence the
loop is alive every minute, vs. the legacy "trigger-or-silence"
mode where a quiet hour was indistinguishable from a stuck loop.
"""

from __future__ import annotations

import logging
from typing import Any

from media_stack.application.jobs.framework import JobContext


logger = logging.getLogger(__name__)


def guardrails_evaluate(_ctx: JobContext) -> dict[str, Any]:
    """One guardrail evaluation cycle. Returns the framework-
    expected result dict:

      * ``skipped`` key when the throttle is active — JobRunner
        records the run with terminal status ``skipped``.
      * Otherwise a payload with ``triggers`` / ``actions`` /
        ``elapsed`` for the dashboard's RunDrawer to surface.

    Errors raised by ``tick()`` propagate; JobRunner catches and
    records terminal status ``error`` with the exception message.

    ADR-0008 Phase 2: pulls the process-wide
    ``DownloadLockdownService`` singleton from
    ``LockdownFactory.singleton()`` and threads it into ``tick()``
    so the rule's ``lockdown_engage`` / ``lockdown_release`` actions
    actually pause / resume download clients in production. The
    factory's lazy-build is failure-isolated per-adapter so a
    missing qbit / sab / arr in the deployment doesn't break the
    auto-heal loop. ``LockdownFactory.singleton()`` is the same
    instance the manual ``/api/disk-guardrails`` route module
    pulls — both code paths share state.
    """
    # Lazy import keeps the framework's bootstrap path free of
    # the guardrails registry — tests that construct a stub
    # ``JobRunner`` without the live registry don't accidentally
    # pull in the rule modules.
    from media_stack.application.guardrails.evaluation_loop import tick
    from media_stack.services.lockdown_factory import LockdownFactory

    lockdown_service = LockdownFactory.singleton()
    result = tick(
        record_history=False,
        lockdown_service=lockdown_service,
    )
    if result.get("skipped"):
        # Surface the throttle/state-skip as a terminal-skipped
        # outcome so the framework records it as ``skipped``
        # rather than ``ok``.
        return {"skipped": result.get("skipped"), **result}
    return result
