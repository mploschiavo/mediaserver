"""Job handlers that drive ``satisfy_promises`` from the JobRunner.

Two concrete handlers share the same platform-detection / live-
services / history-emit plumbing through ``OrchestratorJobHandler``:

* ``OrchestratorShadowJobHandler``     — ADR-0003 Phase 4c. One
  dry-run tick called by the auto-heal cycle every 60s. Probes fire
  but ensurers do NOT (modulo ``ORCHESTRATOR_LIVE_SERVICES``).
* ``OrchestratorBootstrapJobHandler`` — ADR-0005 Phase 1. A blocking
  ``satisfy_promises_blocking`` cycle: loops until every promise
  with ``bootstrap_blocking=True`` reaches ``ok``, one fails
  permanently, or the timeout deadline elapses.

Each handler emits exactly ONE ``RunRecord`` per invocation (via
JobRunner's normal lifecycle). Per-promise outcomes live in the
cooldown state file (queryable for "current state of promise X")
and in the orchestrator's INFO logs (grep-able for "tick history").
No 50+ records-per-minute spam.

The module-level ``satisfy_shadow`` and ``satisfy_blocking``
callables are class-bound aliases (ADR-0012) so contract YAMLs
(``handler:
media_stack.application.jobs.orchestrator_satisfy:satisfy_shadow``)
keep resolving against a stable callable; they delegate to the
singletons defined at the foot of this module.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Mapping

from media_stack.application.jobs.framework import JobContext
from media_stack.domain.services.promises import Promise, PromiseAttempt


logger = logging.getLogger(__name__)


_DEFAULT_BOOTSTRAP_TIMEOUT_SECONDS = 240.0
_DEFAULT_BOOTSTRAP_TICK_INTERVAL_SECONDS = 5.0


class OrchestratorJobHandler:
    """Shared base for JobRunner handlers that invoke the orchestrator.

    Holds the env-knob accessors (``ORCHESTRATOR_LIVE_SERVICES``,
    platform detection) and the per-promise emit policy that both
    shadow + bootstrap handlers need. Concrete handlers override
    :meth:`run`.

    Designed for dependency injection in tests: pass a fake
    ``env_provider`` (``Mapping[str, str]``) and ``platform_override``
    to exercise the env-driven knobs without monkey-patching
    ``os.environ``.
    """

    _LIVE_SERVICES_ENV_VAR = "ORCHESTRATOR_LIVE_SERVICES"
    _RUNTIME_OVERRIDE_ENV_VAR = "MEDIA_STACK_RUNTIME"
    _K8S_DETECT_ENV_VAR = "KUBERNETES_SERVICE_HOST"

    def __init__(
        self,
        *,
        env_provider: Mapping[str, str] | None = None,
    ) -> None:
        self._env: Mapping[str, str] = (
            env_provider if env_provider is not None else os.environ
        )

    # ------------------------------------------------------------------
    # Concrete handlers override this. Base just raises.
    # ------------------------------------------------------------------
    def run(self, ctx: JobContext) -> dict[str, Any]:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Shared knobs
    # ------------------------------------------------------------------
    def live_services_from_env(self) -> "frozenset[str] | None":
        """Read the live-services allowlist from ``self._env``. Empty
        or unset → ``None`` (no override). The shadow handler treats
        this set as "ensurers run for real for these services
        instead of being skipped"; the bootstrap handler runs every
        ensurer for real but still passes the value through so
        per-service rollout knobs stay aligned across both code
        paths."""
        raw = (self._env.get(self._LIVE_SERVICES_ENV_VAR) or "").strip()
        if not raw:
            return None
        parts = frozenset(s.strip().lower() for s in raw.split(",") if s.strip())
        return parts or None

    def detect_platform(self) -> str:
        """``compose`` | ``k8s``. K8s exposes
        ``KUBERNETES_SERVICE_HOST`` in every pod automatically;
        compose doesn't. ``MEDIA_STACK_RUNTIME`` is an explicit
        override the deployer can set when the heuristic is wrong."""
        explicit = (self._env.get(self._RUNTIME_OVERRIDE_ENV_VAR) or "").strip().lower()
        if explicit in ("compose", "k8s"):
            return explicit
        if self._env.get(self._K8S_DETECT_ENV_VAR):
            return "k8s"
        return "compose"

    # ADR-0012: plain instance method (no ``@staticmethod``). The
    # signature matches the ``history_emit`` callable contract — the
    # orchestrator passes ``self._no_op_emit`` as the callback, so
    # binding takes care of ``self`` and the orchestrator's
    # ``(promise, attempt, phase)`` invocation lines up.
    def _no_op_emit(
        self,
        promise: Promise | None,
        attempt: PromiseAttempt | None,
        phase: str,
    ) -> None:
        """Discard per-promise records — the JobRunner-level
        RunRecord plus the cooldown state file already carry
        everything operators need."""
        return None

    def _format_live_services(
        self,
        live_services: "frozenset[str] | None",
    ) -> str:
        return (
            ",".join(sorted(live_services))
            if live_services
            else ""
        )


class OrchestratorShadowJobHandler(OrchestratorJobHandler):
    """ADR-0003 Phase 4c: one dry-run orchestrator tick.

    Probes fire so per-promise state advances; ensurers DO NOT run
    unless the matching service id is in
    ``ORCHESTRATOR_LIVE_SERVICES``. Auto-heal invokes this every 60s
    in steady state.
    """

    def run(self, ctx: JobContext) -> dict[str, Any]:
        from media_stack.application.services.orchestrator import (
            satisfy_promises,
        )

        platform = self.detect_platform()
        live_services = self.live_services_from_env()
        summary = satisfy_promises(
            platform=platform,
            dry_run=True,
            live_services=live_services,
            history_emit=self._no_op_emit,
        )

        live_services_str = self._format_live_services(live_services)
        log_method = logger.info if summary.has_failures else logger.debug
        log_method(
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


class OrchestratorBootstrapJobHandler(OrchestratorJobHandler):
    """ADR-0005 Phase 1: blocking ``satisfy_promises_blocking`` cycle.

    Loops single ticks until every promise with
    ``bootstrap_blocking=True`` reaches ``ok``, one of them reaches
    ``failed_permanent``, or the timeout deadline elapses. Bootstrap
    will eventually wire this in as a synthetic post-Phase-A job
    (Phase 2 of the migration); Phase 1 ships the primitive only.

    Status policy on the returned result dict:

    * ``ok``    — every blocking promise reached ``ok`` within the
                  timeout.
    * ``error`` — a blocking promise hit ``failed_permanent`` (the
                  ``permanent_failure_id`` lands on ``error`` so
                  dashboards surface it).
    * ``warn``  — timeout elapsed before convergence. Bootstrap
                  treats this as ``initial_bootstrap_done=True`` with
                  a deferred-followup banner; the orchestrator's
                  per-60s tick keeps trying in the background.

    Knobs (env-var):

    * ``BOOTSTRAP_PROMISE_TIMEOUT``       — float seconds. Default 240.
    * ``BOOTSTRAP_PROMISE_TICK_INTERVAL`` — float seconds. Default 5.
    """

    _TIMEOUT_ENV_VAR = "BOOTSTRAP_PROMISE_TIMEOUT"
    _TICK_INTERVAL_ENV_VAR = "BOOTSTRAP_PROMISE_TICK_INTERVAL"

    def __init__(
        self,
        *,
        env_provider: Mapping[str, str] | None = None,
        default_timeout_seconds: float = _DEFAULT_BOOTSTRAP_TIMEOUT_SECONDS,
        default_tick_interval_seconds: float = _DEFAULT_BOOTSTRAP_TICK_INTERVAL_SECONDS,
    ) -> None:
        super().__init__(env_provider=env_provider)
        self._default_timeout_seconds = default_timeout_seconds
        self._default_tick_interval_seconds = default_tick_interval_seconds

    def run(self, ctx: JobContext) -> dict[str, Any]:
        from media_stack.application.services.orchestrator import (
            satisfy_promises_blocking,
        )

        platform = self.detect_platform()
        live_services = self.live_services_from_env()
        timeout_seconds = self._float_env(
            self._TIMEOUT_ENV_VAR, self._default_timeout_seconds,
        )
        tick_interval_seconds = self._float_env(
            self._TICK_INTERVAL_ENV_VAR, self._default_tick_interval_seconds,
        )

        summary = satisfy_promises_blocking(
            timeout_seconds=timeout_seconds,
            tick_interval_seconds=tick_interval_seconds,
            platform=platform,
            live_services=live_services,
            history_emit=self._no_op_emit,
        )
        return self._build_result(summary, platform, live_services)

    def _build_result(
        self,
        summary: Any,  # BlockingSummary; Any avoids a top-level import dep cycle
        platform: str,
        live_services: "frozenset[str] | None",
    ) -> dict[str, Any]:
        live_services_str = self._format_live_services(live_services)
        if summary.blocking_promises_ok:
            logger.info(
                "[bootstrap:satisfy-promises] %s; platform=%s; live=%s",
                summary.summary_line(), platform,
                live_services_str or "(none)",
            )
            status = "ok"
            error = ""
        elif summary.permanent_failure_id:
            logger.error(
                "[bootstrap:satisfy-promises] %s; platform=%s; live=%s",
                summary.summary_line(), platform,
                live_services_str or "(none)",
            )
            status = "error"
            error = (
                f"blocking promise '{summary.permanent_failure_id}' "
                f"reached failed_permanent"
            )
        else:
            logger.warning(
                "[bootstrap:satisfy-promises] %s; platform=%s; live=%s",
                summary.summary_line(), platform,
                live_services_str or "(none)",
            )
            status = "warn"
            error = (
                f"timeout after {summary.elapsed_seconds:.1f}s; "
                f"orchestrator continuous-mode will keep retrying"
            )

        final = summary.final_summary
        result: dict[str, Any] = {
            "status": status,
            "platform": platform,
            "elapsed": round(summary.elapsed_seconds, 3),
            "ticks": summary.ticks,
            "timed_out": summary.timed_out,
            "blocking_promises_ok": summary.blocking_promises_ok,
            "live_services": live_services_str,
            "total": final.total,
            "ok_count": final.ok,
            "failed_transient_count": final.failed_transient,
            "failed_permanent_count": final.failed_permanent,
            "dep_failed_count": final.dep_failed,
            "skipped_cooldown_count": final.skipped_cooldown,
            "skipped_platform_count": final.skipped_platform,
            "unknown_count": final.unknown,
        }
        if error:
            result["error"] = error
        if summary.permanent_failure_id:
            result["permanent_failure_id"] = summary.permanent_failure_id
        return result

    def _float_env(self, name: str, default: float) -> float:
        raw = (self._env.get(name) or "").strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            logger.warning(
                "[bootstrap:satisfy-promises] ignoring non-numeric %s=%r; "
                "using default %.1f",
                name, raw, default,
            )
            return default


class OrchestratorSatisfyShims:
    """Class-based home for the contract-resolved entrypoints
    ``satisfy_shadow`` / ``satisfy_blocking`` (ADR-0012).

    Each method delegates to the matching module-level singleton
    handler. The module-level aliases below bind to these instance
    methods so contract YAMLs that name a function path keep
    resolving to a stable callable.
    """

    def satisfy_shadow(self, ctx: JobContext) -> dict[str, Any]:
        """Module-level entrypoint for the
        ``orchestrator:satisfy-shadow`` contract job. Delegates to
        the singleton :class:`OrchestratorShadowJobHandler`."""
        # Dispatch through the module so tests that
        # ``mock.patch`` ``_shadow_handler`` keep intercepting
        # (ADR-0012 design principle 3).
        _module = sys.modules[__name__]
        return _module._shadow_handler.run(ctx)

    def satisfy_blocking(self, ctx: JobContext) -> dict[str, Any]:
        """Module-level entrypoint for the future
        ``bootstrap:satisfy-promises`` contract job (ADR-0005 Phase 1
        scaffolding). Delegates to the singleton
        :class:`OrchestratorBootstrapJobHandler`."""
        _module = sys.modules[__name__]
        return _module._bootstrap_handler.run(ctx)


# Module-level singletons — contract YAMLs reference the function
# names below as ``handler: ...orchestrator_satisfy:satisfy_shadow``.
# Keep the function-call form so the dispatcher can resolve a stable
# callable; instantiate the handler once at import time.

_shadow_handler = OrchestratorShadowJobHandler()
_bootstrap_handler = OrchestratorBootstrapJobHandler()


# ADR-0012: zero top-level FunctionDef. The historical
# ``satisfy_shadow`` / ``satisfy_blocking`` callables are now bound
# instance methods of ``OrchestratorSatisfyShims``, exposed at module
# scope through ``_INSTANCE`` so contract YAMLs (``handler:
# …orchestrator_satisfy:satisfy_shadow``) keep resolving unchanged.
_INSTANCE = OrchestratorSatisfyShims()

satisfy_shadow = _INSTANCE.satisfy_shadow
satisfy_blocking = _INSTANCE.satisfy_blocking


__all__ = [
    "OrchestratorBootstrapJobHandler",
    "OrchestratorJobHandler",
    "OrchestratorSatisfyShims",
    "OrchestratorShadowJobHandler",
    "satisfy_blocking",
    "satisfy_shadow",
]
