"""Read-only view of the orchestrator's persisted promise state.

Backs ``GET /api/orchestrator/promises/state``. The ``FreshInstallVerifier``
(ADR-0004) reads this endpoint instead of running a parallel probe
loop from the operator's host shell, so the verifier's "did the
fresh install actually work?" answer is the same answer the live
auto-heal cycle is using.

The reader is pure: takes optional path / now / platform overrides
so tests can pin behavior without monkey-patching env or filesystem.

Returned body follows ``tests/fixtures/orchestrator/
promises_state_endpoint.schema.json``. Two non-200 modes both carry
``last_tick_age_seconds`` so the verifier can distinguish "no state
yet" (saved_at=None) from "state is stale" (saved_at present, age
> threshold) and back off accordingly.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from media_stack.application.jobs.orchestrator_satisfy import (
    OrchestratorJobHandler,
)
from media_stack.infrastructure.promises.cooldown import default_state_path


logger = logging.getLogger(__name__)


_STALE_THRESHOLD_SECONDS = 120.0


class OrchestratorStateReader:
    """Build the response payload for
    ``GET /api/orchestrator/promises/state``.

    Pure / stateless: every override comes through the call site so
    tests pin behaviour without monkeypatching env or filesystem.
    Construct once at module load; the public ``read`` method is the
    single supported entrypoint.
    """

    def __init__(
        self,
        *,
        stale_threshold_seconds: float = _STALE_THRESHOLD_SECONDS,
        env_handler: OrchestratorJobHandler | None = None,
    ) -> None:
        self._default_stale_threshold = stale_threshold_seconds
        # ``OrchestratorJobHandler`` owns the platform-detection and
        # live-services-env parsing logic; reusing one instance keeps
        # the runtime answers aligned with what the orchestrator
        # itself sees.
        self._env_handler = env_handler or OrchestratorJobHandler()

    def read(
        self,
        *,
        now: Optional[float] = None,
        path: Optional[Path] = None,
        platform: Optional[str] = None,
        live_services: Optional[frozenset[str]] = None,
        stale_threshold_seconds: Optional[float] = None,
    ) -> tuple[int, dict[str, Any]]:
        """Build the response payload for
        ``GET /api/orchestrator/promises/state``.

        Returns ``(status_code, body)``:

          * ``(200, payload)`` when the persisted file exists and
            was written within ``stale_threshold_seconds``.
          * ``(503, payload)`` when the file is missing, malformed,
            or older than the threshold. The 503 body still carries
            ``last_tick_age_seconds`` (or ``None`` if no saved_at)
            so the verifier can choose to retry without reparsing
            the body.

        All three orchestrator-runtime fields (``platform``,
        ``live_services``, the cooldown file path) can be overridden
        for tests; the defaults read from the same env / FS the
        orchestrator itself reads.
        """
        now_seconds = time.time() if now is None else float(now)
        state_path = path or default_state_path()
        threshold = (
            self._default_stale_threshold
            if stale_threshold_seconds is None
            else float(stale_threshold_seconds)
        )
        resolved_platform = (
            self._env_handler.detect_platform()
            if platform is None
            else platform
        )
        resolved_live = (
            (self._env_handler.live_services_from_env() or frozenset())
            if live_services is None
            else live_services
        )

        if not state_path.is_file():
            return 503, {
                "error": "orchestrator state not yet persisted",
                "saved_at": None,
                "last_tick_age_seconds": None,
                "platform": resolved_platform,
                "live_services": sorted(resolved_live),
            }

        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("promise_state.json unreadable: %s", exc)
            return 503, {
                "error": (
                    f"orchestrator state unreadable: "
                    f"{exc.__class__.__name__}"
                ),
                "saved_at": None,
                "last_tick_age_seconds": None,
                "platform": resolved_platform,
                "live_services": sorted(resolved_live),
            }

        saved_at_raw = raw.get("saved_at")
        saved_at = (
            float(saved_at_raw)
            if isinstance(saved_at_raw, (int, float))
            else None
        )
        age = (
            (now_seconds - saved_at) if saved_at is not None else None
        )

        if saved_at is None or age is None or age > threshold:
            return 503, {
                "error": (
                    "orchestrator state stale (controller mid-restart?)"
                ),
                "saved_at": saved_at,
                "last_tick_age_seconds": age,
                "platform": resolved_platform,
                "live_services": sorted(resolved_live),
            }

        attempts_dict = raw.get("attempts") or {}
        if not isinstance(attempts_dict, dict):
            attempts_dict = {}

        attempts_list: list[dict[str, Any]] = []
        status_counts: Counter[str] = Counter()
        for promise_id in sorted(attempts_dict):
            entry = attempts_dict[promise_id]
            if not isinstance(entry, dict):
                continue
            attempt_status = str(entry.get("status") or "unknown")
            status_counts[attempt_status] += 1
            attempts_list.append({
                "promise_id": str(
                    entry.get("promise_id") or promise_id,
                ),
                "status": attempt_status,
                "started_at": float(entry.get("started_at") or 0.0),
                "elapsed_seconds": float(
                    entry.get("elapsed_seconds") or 0.0,
                ),
                "detail": str(entry.get("detail") or ""),
                "probe_evidence": dict(
                    entry.get("probe_evidence") or {},
                ),
                "ensurer_fired": bool(
                    entry.get("ensurer_fired") or False,
                ),
                "ensurer_attempts": int(
                    entry.get("ensurer_attempts") or 0,
                ),
                "consecutive_failures": int(
                    entry.get("consecutive_failures") or 0,
                ),
            })

        body = {
            "version": int(raw.get("version") or 1),
            "saved_at": saved_at,
            "last_tick_age_seconds": age,
            "platform": resolved_platform,
            "live_services": sorted(resolved_live),
            "totals": {
                "total": len(attempts_list),
                "ok": status_counts.get("ok", 0),
                "failed_transient": status_counts.get(
                    "failed_transient", 0,
                ),
                "failed_permanent": status_counts.get(
                    "failed_permanent", 0,
                ),
                "dep_failed": status_counts.get("dep_failed", 0),
                "skipped_cooldown": status_counts.get(
                    "skipped_cooldown", 0,
                ),
                "skipped_platform": status_counts.get(
                    "skipped_platform", 0,
                ),
                "unknown": status_counts.get("unknown", 0),
            },
            "attempts": attempts_list,
        }
        return 200, body


_INSTANCE = OrchestratorStateReader()

# Module-level alias — preserves the legacy ``read_state(...)``
# callsites (`from ... import read_state`) and the
# ``mock.patch("...orchestrator_state.read_state")`` pattern in
# ``test_api_server_handlers``. Bound to the ``OrchestratorStateReader``
# instance so callers don't need to know about the class indirection.
read_state = _INSTANCE.read


__all__ = ["OrchestratorStateReader", "read_state"]
