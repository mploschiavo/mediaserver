"""ControllerServeWiring — background subsystem wiring for the serve mode.

ADR-0015 Phase 7e. Pre-Phase-7e the 683-LoC ``_run_serve``
method wired six background subsystems inline: telemetry,
media integrity, config-snapshot timer, scheduler loop,
TriggerEngine, and the auto-run-bootstrap branch. Phase 7e's
first pass moved most of ``_run_serve`` onto ``ControllerServeCommand``
helper methods, but that took the class over the
:envvar:`CLASSES_OVER_15_METHODS_RATCHET` threshold.

This class collects the background-subsystem wiring methods
under one SRP cluster — every method here installs a daemon
thread or subsystem that runs alongside the API server but
doesn't block the boot path.
"""

from __future__ import annotations

import argparse
import os
import threading
from pathlib import Path
from typing import Any, Callable

import media_stack.services.runtime_platform as runtime_platform

from media_stack.api.preflight.profile_validation import validate_profile
from media_stack.api.services import scheduler as _sched_mod
from media_stack.api.services.media_integrity_handlers import (
    _instance as _media_integrity_api,
)
from media_stack.application.jobs import framework as _jf_module
from media_stack.application.jobs.controller_state_accessor import (
    ControllerStateAccessor,
)
from media_stack.application.jobs.framework_predicates import (
    FrameworkPredicates,
)
from media_stack.application.jobs.trigger_dispatcher import (
    TriggerDispatchService,
    TriggerDispatcherSingleton,
)
from media_stack.application.jobs.trigger_engine import TriggerEngine
from media_stack.cli.workflows.controller_scheduler_seeding import (
    ControllerSchedulerSeedService,
)
from media_stack.cli.workflows.maintenance_service import (
    ConfigSnapshotService,
    StaleFilePruner,
)
from media_stack.services.media_integrity.factory import (
    build_default_service as _build_media_integrity,
)
from media_stack.services.telemetry_client import start_telemetry_timer


_FIRST_SNAPSHOT_DELAY_SECONDS = 60
_DEFAULT_SNAPSHOT_INTERVAL_SECONDS = 3600
_SCHEDULER_BOOTSTRAP_GRACE_SECONDS = 120
_SCHEDULER_TICK_SECONDS = 60


class ControllerServeWiring:
    """Background subsystem wiring for serve mode (telemetry, integrity,
    snapshot timer, scheduler loop, trigger engine, auto-run bootstrap).

    All methods are fire-and-forget — they start daemon threads or
    install singletons, then return. The caller is the boot path in
    :class:`ControllerServeCommand._run_serve`.
    """

    def __init__(self, scheduler_seeds: ControllerSchedulerSeedService) -> None:
        self._scheduler_seeds = scheduler_seeds

    def start_telemetry(self) -> None:
        start_telemetry_timer(log=runtime_platform.log)

    def wire_media_integrity(self) -> None:
        try:
            _mi_service = _build_media_integrity()
            _media_integrity_api.set_service(_mi_service)
            runtime_platform.log(
                "[INFO] Media integrity: service ready "
                "(driven by JobRunner; legacy scheduler removed)"
            )
        except FileNotFoundError as exc:
            runtime_platform.log(
                f"[WARN] Media integrity: policy contract missing "
                f"({exc}); subsystem disabled"
            )
        except Exception as exc:  # noqa: BLE001 — defensive; must not block boot
            runtime_platform.log(
                f"[WARN] Media integrity: init failed ({exc}); subsystem disabled"
            )

    def start_snapshot_timer(self, args: argparse.Namespace) -> None:
        snapshot_interval = int(
            os.environ.get(
                "CONFIG_SNAPSHOT_INTERVAL_SECONDS",
                str(_DEFAULT_SNAPSHOT_INTERVAL_SECONDS),
            )
        )
        if snapshot_interval <= 0:
            return
        config_root_path = self._resolve_config_root_path(args)
        snapshot_service = ConfigSnapshotService(config_root=config_root_path)
        prune_service = StaleFilePruner(
            config_root=config_root_path, log=runtime_platform.log,
        )

        def _snapshot_timer() -> None:
            import time as _t
            _t.sleep(_FIRST_SNAPSHOT_DELAY_SECONDS)
            while True:
                try:
                    snapshot_service.snapshot()
                except Exception as exc:  # noqa: BLE001 — background timer
                    runtime_platform.log(f"[WARN] Config snapshot failed: {exc}")
                try:
                    prune_service.prune()
                except Exception as exc:  # noqa: BLE001 — background timer
                    runtime_platform.log(f"[WARN] Stale file cleanup failed: {exc}")
                _t.sleep(snapshot_interval)

        threading.Thread(
            target=_snapshot_timer, daemon=True, name="config-snapshots",
        ).start()

    def start_scheduler_loop(self, action_trigger: Callable[..., None]) -> None:
        def _loop() -> None:
            import time as _t
            self._scheduler_seeds.seed_defaults()
            _t.sleep(_SCHEDULER_BOOTSTRAP_GRACE_SECONDS)
            while True:
                try:
                    due = _sched_mod.get_due_actions()
                    for entry in due:
                        action = entry.get("action") or ""
                        if not action:
                            continue
                        runtime_platform.log(
                            f"[INFO] Scheduler: firing '{action}' "
                            f"(every {entry.get('interval_seconds')}s)"
                        )
                        try:
                            action_trigger(action, {"_triggered_by": "scheduler"})
                        except Exception as exc:  # noqa: BLE001 — dispatch can fail
                            runtime_platform.log(
                                f"[WARN] Scheduler dispatch '{action}' failed: {exc}"
                            )
                except Exception as exc:  # noqa: BLE001 — tick failures non-fatal
                    runtime_platform.log(f"[WARN] Scheduler tick failed: {exc}")
                _t.sleep(_SCHEDULER_TICK_SECONDS)

        threading.Thread(
            target=_loop, daemon=True, name="scheduler-dispatch",
        ).start()

    def wire_trigger_engine(
        self, state: object, action_trigger: Callable[..., None],
    ) -> None:
        try:
            ControllerStateAccessor.set(state)
            FrameworkPredicates.install(state=state)
            FrameworkPredicates.register_all()

            _trigger_engine = TriggerEngine(_jf_module.discover_jobs_from_contracts())
            _trigger_engine.validate_when_predicates_now()
            TriggerDispatcherSingleton.set(
                TriggerDispatchService(
                    _trigger_engine,
                    run_fn=lambda name: action_trigger(
                        name, {"_triggered_by": "trigger"},
                    ),
                ),
            )
            existing_actions = {
                s.get("action")
                for s in _sched_mod.get_schedules().get("schedules") or []
            }

            def _register_if_new(**payload: Any) -> None:
                if payload.get("action") in existing_actions:
                    return
                _sched_mod.add_schedule(
                    action=payload["action"],
                    interval_seconds=payload.get("interval_seconds", 0),
                    label=f"trigger-driven: {payload['action']}",
                )

            _trigger_engine.register_schedules(_register_if_new)
            runtime_platform.log(
                f"[INFO] TriggerEngine ready — "
                f"{len(_trigger_engine.event_kinds())} event kinds indexed"
            )
        except Exception as exc:  # noqa: BLE001 — non-fatal; controller still serves
            runtime_platform.log(
                f"[WARN] TriggerEngine wiring failed: {exc} — "
                "controller will run without trigger-driven recovery"
            )

    def _resolve_config_root_path(self, args: argparse.Namespace) -> Path:
        return Path(
            getattr(args, "config_root", os.environ.get("CONFIG_ROOT", "/srv-config"))
        )

    def maybe_auto_run_bootstrap(
        self,
        args: argparse.Namespace,
        state: object,
        action_trigger: Callable[..., None],
    ) -> None:
        auto_run_requested = (
            args.auto_run or os.environ.get("FULLY_PRECONFIGURED") == "1"
        )
        if auto_run_requested and not state.initial_bootstrap_done:
            runtime_platform.log("[INFO] Auto-run: queuing initial bootstrap action")
            action_trigger("bootstrap", {})

    def reload_config_closure(
        self, apply_profile_env: Callable[[str], None],
    ) -> Callable[[], None]:
        """Build the ``reload_config`` closure handed to ``start_api_server``.

        ``apply_profile_env`` is constructor-injected from the
        commands-tier ``controller_profile`` module so this workflows
        class doesn't reach across the layer boundary.
        """
        def reload_config() -> None:
            pf = os.environ.get("BOOTSTRAP_PROFILE_FILE")
            if pf:
                validate_profile(pf, log=runtime_platform.log)
                apply_profile_env(pf)
            runtime_platform.log("[OK] Config reloaded from profile")
        return reload_config


__all__ = ["ControllerServeWiring"]
