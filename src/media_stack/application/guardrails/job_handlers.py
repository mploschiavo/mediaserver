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

import importlib
import logging
from typing import Any

from media_stack.application.jobs.framework import JobContext


logger = logging.getLogger(__name__)


class GuardrailsJobHandler:
    """Job-framework handler for the ``guardrails:evaluate`` cycle.

    Bridges the auto-heal scheduler's ``run_job`` call onto the
    guardrails ``evaluation_loop.tick`` driver. ADR-0008 Phase 2
    pulls the process-wide ``DownloadLockdownService`` singleton
    via ``LockdownFactory.singleton()`` and threads it into the
    tick so the rule's ``lockdown_engage`` / ``lockdown_release``
    actions actually pause/resume download clients in production.
    """

    def guardrails_evaluate(self, _ctx: JobContext) -> dict[str, Any]:
        """One guardrail evaluation cycle. Returns the framework-
        expected result dict:

          * ``skipped`` key when the throttle is active — JobRunner
            records the run with terminal status ``skipped``.
          * Otherwise a payload with ``triggers`` / ``actions`` /
            ``elapsed`` for the dashboard's RunDrawer to surface.

        Errors raised by ``tick()`` propagate; JobRunner catches
        and records terminal status ``error`` with the exception
        message.

        ADR-0008 Phase 2: pulls the process-wide
        ``DownloadLockdownService`` singleton from
        ``LockdownFactory.singleton()`` and threads it into
        ``tick()`` so the rule's ``lockdown_engage`` /
        ``lockdown_release`` actions actually pause / resume
        download clients in production. The factory's lazy-build
        is failure-isolated per-adapter so a missing qbit / sab /
        arr in the deployment doesn't break the auto-heal loop.
        ``LockdownFactory.singleton()`` is the same instance the
        manual ``/api/disk-guardrails`` route module pulls — both
        code paths share state.
        """
        # Re-import the tick + factory each call so tests that
        # ``monkeypatch.setattr("…evaluation_loop.tick", fake)``
        # intercept reliably; the bound name on this module would
        # cache the original otherwise. Same shape the legacy
        # top-level handler used.
        evaluation_loop = importlib.import_module(
            "media_stack.application.guardrails.evaluation_loop",
        )
        lockdown_factory_module = importlib.import_module(
            "media_stack.services.lockdown_factory",
        )

        lockdown_service = lockdown_factory_module.LockdownFactory.singleton()
        result = evaluation_loop.tick(
            record_history=False,
            lockdown_service=lockdown_service,
        )
        if result.get("skipped"):
            # Surface the throttle/state-skip as a terminal-skipped
            # outcome so the framework records it as ``skipped``
            # rather than ``ok``.
            return {"skipped": result.get("skipped"), **result}
        return result


_INSTANCE = GuardrailsJobHandler()

# Module-level alias preserves the legacy public-import + contract-handler
# path (``…job_handlers:guardrails_evaluate``) registered in
# ``contracts/services/guardrails.yaml`` and imported directly by tests.
guardrails_evaluate = _INSTANCE.guardrails_evaluate
