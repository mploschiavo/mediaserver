"""ControllerOneshotRunner — cron-mode bootstrap entry-point (legacy path).

ADR-0015 Phase 7j. Pre-Phase-7j the one-shot bootstrap orchestration
lived as a ``@staticmethod _run_oneshot`` on ``ControllerMainCommand``
in commands/. That path is invoked by the three k8s CronJobs
(``media-stack-controller-reconcile``, ``media-stack-jellyfin-prewarm``,
``media-stack-media-hygiene``) — each launches ``controller.py``
with a different ``--mode`` flag.

Phase 7j moves the cron-mode workflow onto this class as a proper
instance method; the commands entry-point shrinks to argparse +
dispatch.

The wrapper stamps a ``cron:<mode>`` history entry so
``GET /api/jobs.history`` shows a "ran via cron" badge for each
invocation. The legacy adapter-pipeline ``runner.run`` path doesn't
write to history on its own (it predates the framework); the
wrapper here synthesises a one-line history entry rather than
retrofitting every legacy step writer.
"""

from __future__ import annotations

import argparse
import time

from media_stack.services.jobs.controller_runner import _build_runner
from media_stack.services.jobs.framework import _record_history


class ControllerOneshotRunner:
    """Workflow runner: legacy one-shot bootstrap with history-write wrapper."""

    def run(self, args: argparse.Namespace) -> None:
        mode = str(getattr(args, "mode", "") or "full").strip()
        source_tag = f"cron:{mode}" if mode else "cron"
        runner, runtime_state = _build_runner(args)
        t0 = time.time()
        error: Exception | None = None
        try:
            runner.run(runtime_state)
        except Exception as exc:  # noqa: BLE001 — legacy pipeline raises any type
            error = exc
            raise
        finally:
            elapsed = round(time.time() - t0, 2)
            self._record_history_entry(mode, source_tag, elapsed, error)

    def _record_history_entry(
        self,
        mode: str,
        source_tag: str,
        elapsed: float,
        error: Exception | None,
    ) -> None:
        """Best-effort cron-mode history entry; never masks the real error."""
        try:
            _record_history(
                {
                    "elapsed": elapsed,
                    "ok": 0 if error else 1,
                    "skipped": 0,
                    "errors": 1 if error else 0,
                    "jobs": {
                        f"controller-{mode or 'full'}": {
                            "status": "error" if error else "ok",
                            "elapsed": elapsed,
                        },
                    },
                },
                source=source_tag,
            )
        except Exception:  # noqa: BLE001 — history write must never mask the real error
            pass


__all__ = ["ControllerOneshotRunner"]
