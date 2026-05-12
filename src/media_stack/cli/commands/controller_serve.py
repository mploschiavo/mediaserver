"""HTTP API serve mode for the bootstrap controller.

ADR-0015 Phase 7m refactor
--------------------------

Pre-Phase-7m the boot-prep helpers (``_resolve_config_path``,
``_opt_out_of_legacy_media_server_adapter``, ``_apply_boot_profile``,
``_predispatch_api_keys``) lived on :class:`ControllerServeCommand`
here in commands/. Phase 7m moves them onto
:class:`ControllerBootPreparation` under
``cli/workflows/controller_boot/``. The serve command now wires the
preparation class via constructor injection and calls it from
``_run_serve``.

ADR-0015 Phase 7e refactor
--------------------------

Pre-Phase-7e ``_run_serve`` was 683 LoC — by a wide margin the
worst long-method offender in the codebase. The body mixed
legitimate HTTP-server-glue (start_api_server, action_trigger
queue-injection seam, log instrumentation) with workflow logic
that belonged in workflows/.

Phase 7e extracted the workflow logic onto SRP classes under
``cli/workflows/``:

* :class:`KeyCanaryValidator` + :class:`BootProfileLoader` +
  :class:`BootConfigureAuthService` (``controller_boot/``) —
  boot-time auth setup + config-mount canary.
* :class:`ControllerSchedulerSeedService` — seeds the controller's
  seven default schedules. Idempotent across restarts.
* :class:`ControllerActionDispatcher` (+ :class:`ActionWatchdog`
  + :class:`SingleActionRunner`) — drains the priority queue,
  enforces the per-action timeout, retries on failure, fires
  webhooks. Replaces the ``_action_loop`` + ``_run_one_action``
  + ``_watchdog`` closure forest.
* :class:`ControllerServeWiring` — background subsystem wiring
  (telemetry, media integrity, snapshot timer, scheduler loop,
  trigger engine, auto-run bootstrap).

What stays in this file (per ADR-0015's HTTP-tier exemption):

* The argparse + entry-point shim (``main``).
* The ``action_trigger`` closure — every config writer in
  ``routes/post_*`` and the ``auth_config`` service depends on
  it as the queue-injection seam, so it must be in the same
  scope as the queue itself.
* The log-instrumentation wrapper — feeds the SSE ring buffer
  on ``ControllerState`` for ``GET /logs/stream``.
* The threading orchestration (action thread + main thread join).
"""

from __future__ import annotations

import argparse
import os
import queue
import sys
import threading

import media_stack.services.runtime_platform as runtime_platform

from media_stack.api.server import (
    ACTION_PRIORITY,
    DEFAULT_ACTION_PRIORITY,
    _fire_webhooks,
    start_api_server,
)
from media_stack.api.state import ControllerState
from media_stack.cli.commands.controller_dispatch import _dispatch_action
from media_stack.cli.commands.controller_profile import _apply_profile_env
from media_stack.cli.workflows.controller_action_dispatcher import (
    ControllerActionDispatcher,
)
from media_stack.cli.workflows.controller_boot import (
    BootConfigureAuthService,
    BootProfileLoader,
    ControllerBootPreparation,
    KeyCanaryValidator,
)
from media_stack.cli.workflows.controller_scheduler_seeding import (
    ControllerSchedulerSeedService,
)
from media_stack.cli.workflows.controller_serve_wiring import (
    ControllerServeWiring,
)


_DEFAULT_API_PORT = 9100
_DEFAULT_ACTION_TIMEOUT_SECONDS = 1800


class ControllerServeCommand:
    """HTTP API serve mode — argparse + boot-orchestration + thread wiring."""

    def __init__(self) -> None:
        self._key_canary = KeyCanaryValidator()
        self._boot_profile_loader = BootProfileLoader(log=runtime_platform.log)
        self._boot_configure_auth = BootConfigureAuthService(
            self._boot_profile_loader, log=runtime_platform.log,
        )
        self._scheduler_seeds = ControllerSchedulerSeedService(
            log=runtime_platform.log,
        )
        self._wiring = ControllerServeWiring(self._scheduler_seeds)
        self._boot_prep = ControllerBootPreparation(
            key_canary=self._key_canary, log=runtime_platform.log,
        )

    # -- backward-compat shims for the pre-Phase-7e module-level aliases ---

    def _validate_key_against_service(
        self, discovered: dict, config_root: str, log: object,
    ) -> None:
        self._key_canary.validate(discovered, config_root, log)

    def _run_boot_configure_auth(self, state: object) -> None:
        del state  # reserved for future hooks; not needed to read profile.
        self._boot_configure_auth.run(dict(os.environ))

    def _load_boot_profile(self, env: dict) -> dict:
        return self._boot_profile_loader.load(env)

    # -- serve-mode boot orchestration ------------------------------------

    def _run_serve(self, args: argparse.Namespace) -> None:
        """HTTP API server with action dispatch loop.

        The server stays alive indefinitely, processing actions from a queue.
        Actions are triggered via POST /actions/{name} or POST /run.
        """
        module = sys.modules[__name__]

        self._boot_prep.resolve_config_path(args)
        self._boot_prep.opt_out_of_legacy_media_server_adapter()
        self._boot_prep.apply_boot_profile(args)
        self._boot_prep.predispatch_api_keys(args)

        state = ControllerState()
        state.load_persisted_config()
        module._run_boot_configure_auth(state)

        action_queue, action_trigger = self._build_action_queue()
        self._instrument_log(state)
        port = self._resolve_api_port(args)
        server = start_api_server(
            state, port=port,
            action_trigger=action_trigger,
            reload_config=self._wiring.reload_config_closure(_apply_profile_env),
        )
        runtime_platform.log(f"[INFO] Bootstrap service listening on :{port}")
        runtime_platform.log(f"[INFO] Dashboard: http://127.0.0.1:{port}/")
        runtime_platform.log("[INFO] Actions: POST /actions/{name} | GET /status")
        runtime_platform.log("[INFO] SSE log stream: GET /logs/stream")

        self._wiring.start_telemetry()
        self._wiring.wire_media_integrity()
        self._wiring.start_snapshot_timer(args)
        self._wiring.start_scheduler_loop(action_trigger)
        self._wiring.wire_trigger_engine(state, action_trigger)
        self._wiring.maybe_auto_run_bootstrap(args, state, action_trigger)
        self._run_action_dispatch_loop(
            args, state, server, action_queue, _fire_webhooks,
        )

    # -- HTTP-server glue (kept in commands/ per ADR-0015 exemption) ------

    def _resolve_api_port(self, args: argparse.Namespace) -> int:
        return int(
            args.api_port
            or os.environ.get("BOOTSTRAP_API_PORT", str(_DEFAULT_API_PORT)),
        )

    def _build_action_queue(self):
        action_queue: queue.PriorityQueue[tuple[int, int, str, dict]] = (
            queue.PriorityQueue()
        )
        seq_counter = [0]
        seq_lock = threading.Lock()

        def action_trigger(action_name: str, overrides: dict) -> None:
            prio = int(
                overrides.pop(
                    "_priority",
                    ACTION_PRIORITY.get(action_name, DEFAULT_ACTION_PRIORITY),
                )
            )
            with seq_lock:
                seq_counter[0] += 1
                seq = seq_counter[0]
            action_queue.put((prio, seq, action_name, overrides))

        return action_queue, action_trigger

    def _instrument_log(self, state: object) -> None:
        original_log = runtime_platform.log

        def _instrumented_log(msg: str) -> None:
            original_log(msg)
            state.append_log(msg)

        runtime_platform.log = _instrumented_log

    def _run_action_dispatch_loop(
        self,
        args: argparse.Namespace,
        state: object,
        server: object,
        action_queue: "queue.PriorityQueue",
        fire_webhooks,
    ) -> None:
        action_timeout = int(
            os.environ.get(
                "BOOTSTRAP_ACTION_TIMEOUT",
                str(_DEFAULT_ACTION_TIMEOUT_SECONDS),
            )
        )
        max_retries = int(os.environ.get("BOOTSTRAP_ACTION_MAX_RETRIES", "0"))
        dispatcher = ControllerActionDispatcher(
            action_queue=action_queue,
            args=args,
            state=state,
            server=server,
            action_timeout_seconds=action_timeout,
            max_retries=max_retries,
            log=runtime_platform.log,
            fire_webhooks=fire_webhooks,
            dispatch_action=_dispatch_action,
        )
        action_thread = threading.Thread(
            target=dispatcher.drain_forever, daemon=True, name="action-dispatch",
        )
        action_thread.start()
        # Park the main thread on the action thread join. The action
        # thread is a daemon so it dies with us. KeyboardInterrupt
        # bubbles up here and shuts the API server.
        try:
            action_thread.join()
        except KeyboardInterrupt:
            runtime_platform.log("[INFO] Shutting down bootstrap service")
            server.shutdown()


_INSTANCE = ControllerServeCommand()
_validate_key_against_service = _INSTANCE._validate_key_against_service
_run_boot_configure_auth = _INSTANCE._run_boot_configure_auth
_load_boot_profile = _INSTANCE._load_boot_profile
_run_serve = _INSTANCE._run_serve
