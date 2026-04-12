"""HTTP API serve mode for the bootstrap controller."""

from __future__ import annotations

import argparse
import multiprocessing
import os
import queue
import threading
import traceback

import media_stack.services.runtime_platform as runtime_platform

from media_stack.cli.commands.controller_dispatch import (
    _dispatch_action,
    _track_failed_service,
)
from media_stack.cli.commands.controller_handlers import (
    _resolve_config_path,
)
from media_stack.cli.commands.controller_profile import (
    _apply_profile_env,
)


def _validate_key_against_service(discovered: dict, config_root: str, log: object) -> None:
    """Quick check: does a discovered key actually work against the running service?

    If not, the controller's config mount likely points to a different directory
    than the services. This is a common compose context mismatch.
    """
    import urllib.request
    import urllib.error

    # Pick the first arr app with a discovered key as the canary for mount validation
    from media_stack.api.services.registry import SERVICES
    canary = None
    canary_key = ""
    for svc in SERVICES:
        if svc.api_key_env and svc.auth_path and svc.api_key_format == "xml":
            canary_key = discovered.get(svc.api_key_env, "")
            if canary_key:
                canary = svc
                break
    if not canary:
        return
    try:
        req = urllib.request.Request(
            f"http://{canary.host}:{canary.port}{canary.auth_path}",
            headers={canary.auth_mode: canary_key},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                return  # Key works — mounts are consistent
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            log(
                f"[WARN] Config mount mismatch detected: API key from "
                f"{config_root}/{canary.api_key_config} does not match the running "
                f"{canary.name} container. This usually means the controller and "
                "services are using different config directories. "
                "Re-run 'docker compose down && docker compose up -d' from "
                "the same directory to fix."
            )
            return
    except Exception:
        pass  # Service not ready yet — skip validation


def _run_serve(args: argparse.Namespace) -> None:
    """HTTP API server with action dispatch loop.

    The server stays alive indefinitely, processing actions from a queue.
    Actions are triggered via POST /actions/{name} or POST /run.
    """
    from media_stack.api.server import _fire_webhooks, start_api_server
    from media_stack.api.state import ControllerState

    # Resolve config path: try CLI arg, env var, then image-embedded path.
    resolved = _resolve_config_path(args.config)
    if resolved and resolved != args.config:
        runtime_platform.log(
            f"[INFO] Config resolved: {args.config} → {resolved}"
        )
        args.config = resolved
    elif not resolved:
        # Config JSON not found — generate from contracts + profile
        # This eliminates the need for a pre-built config JSON in compose mode
        runtime_platform.log("[INFO] Bootstrap config JSON not found — generating from contracts + profile")
        try:
            from media_stack.cli.commands.controller_handlers import _auto_generate_config_json
            generated = _auto_generate_config_json(args.config)
            if generated:
                args.config = generated
                runtime_platform.log(f"[OK] Generated config from contracts: {generated}")
        except Exception as exc:
            runtime_platform.log(f"[WARN] Config generation failed: {exc}. Bootstrap may skip some steps.")

    # Media server ops are handled by the configure-media-server job framework.
    # Skip the old media server adapter in finalize to prevent conflicts
    # (the old adapter reads config.json which has fewer tuners/guides than the profile).
    os.environ["SKIP_MEDIA_SERVER_ADAPTER_IN_FINALIZE"] = "1"

    # Load profile if available (ConfigMap may not be mounted yet on first start).
    profile_file = os.environ.get("BOOTSTRAP_PROFILE_FILE")
    if profile_file:
        profile_path = __import__("pathlib").Path(profile_file)
        if profile_path.is_file():
            from media_stack.api.preflight.profile_validation import validate_profile

            try:
                validate_profile(profile_file, log=runtime_platform.log)
            except Exception as exc:
                runtime_platform.log(
                    f"[WARN] Profile validation failed: {exc}. "
                    "The controller will still start — fix the profile and restart."
                )
            _apply_profile_env(profile_file)
        else:
            runtime_platform.log(
                f"[INFO] Profile not yet available at {profile_file} — "
                "will apply from config when action is triggered"
            )

    # Pre-discover API keys: auto-discovery preflight detects the real
    # CONFIG_ROOT (via Docker mounts/env or path scanning) before falling
    # back to the standard file-based key readers.
    try:
        from media_stack.api.preflight.api_keys import run_preflight as _discover_keys
        config_root = getattr(args, "config_root", os.environ.get("CONFIG_ROOT", "/srv-config"))
        runtime_platform.log(f"[INFO] Config root discovery starting (configured: {config_root})")
        discovered = _discover_keys(config_root=config_root, log=runtime_platform.log)
        # Update CONFIG_ROOT in case discovery changed it
        config_root = os.environ.get("CONFIG_ROOT", config_root)
        for env_key, val in discovered.items():
            if val and not os.environ.get(env_key):
                os.environ[env_key] = val
        if discovered:
            runtime_platform.log(f"[INFO] Pre-discovered {len(discovered)} API keys (config_root={config_root})")
        # Validate a key against a running service to detect mount mismatches
        _validate_key_against_service(discovered, config_root, runtime_platform.log)
    except Exception as exc:
        runtime_platform.log(f"[WARN] API key pre-discovery failed: {exc}")

    state = ControllerState()
    state.load_persisted_config()
    port = int(args.api_port or os.environ.get("BOOTSTRAP_API_PORT", "9100"))
    action_queue: queue.PriorityQueue[tuple[int, int, str, dict]] = queue.PriorityQueue()
    action_timeout = int(os.environ.get("BOOTSTRAP_ACTION_TIMEOUT", "600"))
    max_retries = int(os.environ.get("BOOTSTRAP_ACTION_MAX_RETRIES", "0"))
    _queue_seq = 0

    def action_trigger(action_name: str, overrides: dict) -> None:
        nonlocal _queue_seq
        from media_stack.api.server import ACTION_PRIORITY, DEFAULT_ACTION_PRIORITY
        prio = int(overrides.pop("_priority", ACTION_PRIORITY.get(action_name, DEFAULT_ACTION_PRIORITY)))
        _queue_seq += 1
        action_queue.put((prio, _queue_seq, action_name, overrides))
        state.add_pending(action_name, prio, overrides)

    # Wrap runtime_platform.log to also feed the SSE ring buffer.
    _original_log = runtime_platform.log

    def _instrumented_log(msg: str) -> None:
        _original_log(msg)
        state.append_log(msg)

    runtime_platform.log = _instrumented_log

    def reload_config() -> None:
        """Reload profile YAML and re-apply env vars."""
        pf = os.environ.get("BOOTSTRAP_PROFILE_FILE")
        if pf:
            from media_stack.api.preflight.profile_validation import validate_profile

            validate_profile(pf, log=runtime_platform.log)
            _apply_profile_env(pf)
        runtime_platform.log("[OK] Config reloaded from profile")

    server = start_api_server(
        state, port=port, action_trigger=action_trigger, reload_config=reload_config,
    )
    runtime_platform.log(f"[INFO] Bootstrap service listening on :{port}")
    runtime_platform.log(f"[INFO] Dashboard: http://127.0.0.1:{port}/")
    runtime_platform.log(f"[INFO] Actions: POST /actions/{{name}} | GET /status")
    runtime_platform.log(f"[INFO] SSE log stream: GET /logs/stream")

    # Start telemetry push (if configured)
    from media_stack.services.telemetry_client import start_telemetry_timer
    start_telemetry_timer(log=runtime_platform.log)

    # Start config snapshot background timer
    snapshot_interval = int(os.environ.get("CONFIG_SNAPSHOT_INTERVAL_SECONDS", "3600"))  # 1h default
    if snapshot_interval > 0:
        def _snapshot_timer() -> None:
            import time as _t
            _t.sleep(60)  # Wait 1 min before first snapshot
            while True:
                try:
                    from media_stack.cli.commands.maintenance import take_config_snapshot
                    take_config_snapshot(args)
                except Exception as exc:
                    runtime_platform.log(f"[WARN] Config snapshot failed: {exc}")
                # Prune stale cache/transcode files to prevent disk growth
                try:
                    from media_stack.cli.commands.maintenance import prune_stale_files
                    prune_stale_files(args, runtime_platform.log)
                except Exception as exc:
                    runtime_platform.log(f"[WARN] Stale file cleanup failed: {exc}")
                _t.sleep(snapshot_interval)
        snap_thread = threading.Thread(target=_snapshot_timer, daemon=True, name="config-snapshots")
        snap_thread.start()

    auto_run = args.auto_run or os.environ.get("FULLY_PRECONFIGURED") == "1"
    if auto_run:
        runtime_platform.log("[INFO] Auto-run: queuing initial bootstrap action")
        action_trigger("bootstrap", {})

        # Run media server configuration in a SUBPROCESS — separate GIL
        # so the API server stays responsive during heavy EPG/livetv work.
        def _ms_worker(log_q: multiprocessing.Queue) -> None:
            import media_stack.services.runtime_platform as _rp
            _rp.log = lambda msg: log_q.put(msg)
            try:
                from media_stack.cli.commands.job_framework import run_all_media_server_jobs
                log_q.put("[INFO] Starting media server configuration (background)")
                result = run_all_media_server_jobs(max_wait=180)
                status = result.get("status", "unknown")
                if status == "ok":
                    log_q.put("[OK] Media server configuration complete (background)")
                elif status == "prereq_not_met":
                    log_q.put(f"[WARN] Media server configuration deferred: {result.get('reason')}")
                else:
                    log_q.put(f"[WARN] Media server configuration: {result}")
            except Exception as exc:
                log_q.put(f"[ERR] Media server background configuration: {exc}")
            log_q.put(None)  # sentinel

        ms_log_q: multiprocessing.Queue = multiprocessing.Queue()
        ms_proc = multiprocessing.Process(
            target=_ms_worker, args=(ms_log_q,), daemon=True,
        )
        ms_proc.start()

        # Drain log queue in a lightweight thread (no GIL contention)
        def _drain_ms_logs() -> None:
            while True:
                try:
                    msg = ms_log_q.get(timeout=1)
                    if msg is None:
                        break
                    runtime_platform.log(msg)
                except Exception:
                    if not ms_proc.is_alive():
                        break
        threading.Thread(target=_drain_ms_logs, daemon=True, name="ms-log-drain").start()

    # -----------------------------------------------------------------------
    # Action worker — runs in a SUBPROCESS (separate GIL) so the API
    # server stays responsive while jobs execute.
    #
    # Each action spawns a short-lived child process. The parent monitors
    # it, drains logs, and detects crashes. If the child dies, the parent
    # logs the error and continues processing the queue. The container's
    # healthcheck only checks the parent (API server) which is always up.
    # -----------------------------------------------------------------------

    class _SubprocessState:
        """Lightweight state stub for subprocess workers.

        The real ControllerState can't be pickled across processes.
        This stub absorbs calls that the dispatch code makes on state
        (record_preflight, mark_service_failed, etc.) without crashing.
        """
        preflight_results = {}
        is_cancelled = False

        def __getattr__(self, name):
            """Return a no-op for any method call."""
            return lambda *a, **kw: None

    def _action_worker(
        action_name: str,
        overrides: dict,
        args_dict: dict,
        log_queue: multiprocessing.Queue,
    ) -> None:
        """Run one action in a subprocess. Logs go to log_queue."""
        import argparse as _ap
        import signal as _signal
        import traceback as _tb

        # Register SIGTERM handler so job framework can cancel cooperatively
        # before the process is killed.
        def _on_sigterm(signum, frame):
            from media_stack.cli.commands.job_framework import request_cancel
            request_cancel()
            log_queue.put(("log", f"[ACTION] {action_name}: SIGTERM received, cancelling jobs"))

        _signal.signal(_signal.SIGTERM, _on_sigterm)

        # Reconstruct args namespace from dict
        worker_args = _ap.Namespace(**args_dict)

        # Redirect runtime_platform.log to the queue (preserving level filter)
        import media_stack.services.runtime_platform as _rp

        def _subprocess_log(msg):
            if _rp._extract_level(str(msg)) < _rp._current_log_level:
                return
            log_queue.put(("log", msg))

        _rp.log = _subprocess_log

        # Stub state — absorbs record_preflight etc. without error
        stub_state = _SubprocessState()

        try:
            _dispatch_action(action_name, overrides, worker_args, stub_state)
            log_queue.put(("done", None))
        except Exception as exc:
            log_queue.put(("error", str(exc)))
            tb = _tb.format_exc().strip()
            if tb:
                for line in tb.splitlines():
                    log_queue.put(("log", f"[TRACE] {line}"))

    # Serialize args to a dict for subprocess pickling
    _args_dict = vars(args)

    # Main action dispatch loop — runs forever.
    while True:
        try:
            _prio, _seq, action_name, overrides = action_queue.get()
        except KeyboardInterrupt:
            runtime_platform.log("[INFO] Shutting down bootstrap service")
            server.shutdown()
            return

        state.pop_pending(action_name)

        # Merge runtime_config into overrides so toggles like auto_download_content
        # propagate to _apply_overrides in the subprocess.
        for cfg_key, cfg_val in state.runtime_config.items():
            overrides.setdefault(cfg_key, cfg_val)

        # Retry support: allow per-action retry via override or env default.
        retry_limit = int(overrides.pop("retry", max_retries))
        attempt = 0

        while True:
            attempt += 1
            action_record = state.start_action(
                action_name, overrides=overrides, timeout_seconds=action_timeout
            )
            suffix = f" (attempt {attempt}/{retry_limit + 1})" if retry_limit > 0 else ""
            runtime_platform.log(
                f"[ACTION] {action_name} [{action_record.id}]: dispatching "
                f"(timeout={action_timeout}s){suffix}"
            )

            # Run action in subprocess — separate GIL, API stays responsive
            log_q: multiprocessing.Queue = multiprocessing.Queue()
            runtime_platform.log(f"[DEBUG] Spawning subprocess for action={action_name}, "
                                 f"pid=parent:{os.getpid()}")
            worker = multiprocessing.Process(
                target=_action_worker,
                args=(action_name, dict(overrides), _args_dict, log_q),
                daemon=True,
            )
            worker.start()
            runtime_platform.log(f"[DEBUG] Subprocess started: pid={worker.pid}")

            # Drain log queue while worker runs; check for cancellation.
            error_msg = None
            cancelled = False
            while worker.is_alive() or not log_q.empty():
                # Check cancel request — kill subprocess immediately.
                if not cancelled and state.is_cancelled:
                    cancelled = True
                    runtime_platform.log(f"[ACTION] {action_name}: cancelling (killing pid={worker.pid})")
                    worker.terminate()
                    worker.join(timeout=3)
                    if worker.is_alive():
                        worker.kill()
                        worker.join(timeout=2)
                    break
                try:
                    msg_type, msg_data = log_q.get(timeout=0.5)
                    if msg_type == "log":
                        runtime_platform.log(msg_data)
                    elif msg_type == "done":
                        pass
                    elif msg_type == "error":
                        error_msg = msg_data
                except Exception:
                    pass

            if cancelled:
                state.finish_action(error="cancelled by user")
                runtime_platform.log(f"[ACTION] {action_name}: cancelled")
                break

            worker.join(timeout=5)
            exit_code = worker.exitcode
            runtime_platform.log(f"[DEBUG] Subprocess finished: pid={worker.pid}, "
                                 f"exit_code={exit_code}, error={'yes' if error_msg else 'no'}")

            if error_msg:
                state.finish_action(error=error_msg)
                runtime_platform.log(f"[ERR] Action {action_name} failed: {error_msg}")

                if action_name == "bootstrap" and not state.initial_bootstrap_done:
                    state.initial_bootstrap_done = True
                    runtime_platform.log(
                        "[WARN] Initial bootstrap had errors but service is marked ready"
                    )
                    for queued in ["configure-media-server", "post-setup", "envoy-config", "validate-credentials"]:
                        runtime_platform.log(f"[INFO] Auto-queuing {queued} despite bootstrap error")
                        action_trigger(queued, {})

                if attempt <= retry_limit:
                    delay = min(10.0, 2.0 ** (attempt - 1))
                    runtime_platform.log(
                        f"[RETRY] {action_name}: retrying in {delay:.0f}s "
                        f"(attempt {attempt}/{retry_limit + 1})"
                    )
                    import time as _time
                    _time.sleep(delay)
                    continue

                _fire_webhooks(state, "action_error", {
                    "action": action_name,
                    "status": "error",
                    "error": error_msg,
                    "elapsed_seconds": action_record.elapsed_seconds,
                })

                if state.get_failed_services() and action_name in ("bootstrap", "reconcile"):
                    heal_delay = int(os.environ.get("AUTO_HEAL_DELAY_SECONDS", "120"))
                    runtime_platform.log(
                        f"[HEAL] {len(state.get_failed_services())} services need healing. "
                        f"Auto-queuing reconcile in {heal_delay}s."
                    )
                    threading.Timer(heal_delay, lambda: action_trigger("reconcile", {})).start()

                break  # Exhausted retries.

            else:
                # Success
                state.finish_action()

                _fire_webhooks(state, "action_complete", {
                    "action": action_name,
                    "status": "complete",
                    "elapsed_seconds": action_record.elapsed_seconds,
                })

                if action_name == "bootstrap" and not state.initial_bootstrap_done:
                    state.initial_bootstrap_done = True
                    runtime_platform.log("[INFO] Initial bootstrap complete — service is ready")
                    for queued in ["configure-media-server", "post-setup", "envoy-config", "discover-indexers", "validate-credentials"]:
                        runtime_platform.log(f"[INFO] Auto-queuing {queued} after bootstrap")
                        action_trigger(queued, {})

                break  # Success — exit retry loop.

                # Retry if attempts remain.
                if attempt <= retry_limit:
                    delay = min(10.0, 2.0 ** (attempt - 1))
                    runtime_platform.log(
                        f"[RETRY] {action_name}: retrying in {delay:.0f}s "
                        f"(attempt {attempt}/{retry_limit + 1})"
                    )
                    import time as _time

                    _time.sleep(delay)
                    continue

                # Track failed services for auto-heal.
                _track_failed_service(state, str(exc))

                # Fire webhooks on final failure.
                _fire_webhooks(state, "action_error", {
                    "action": action_name,
                    "status": "error",
                    "error": str(exc),
                    "elapsed_seconds": action_record.elapsed_seconds,
                })

                # Auto-queue reconcile if services failed (heal after delay).
                if state.get_failed_services() and action_name in ("bootstrap", "reconcile"):
                    heal_delay = int(os.environ.get("AUTO_HEAL_DELAY_SECONDS", "120"))
                    runtime_platform.log(
                        f"[HEAL] {len(state.get_failed_services())} services need healing. "
                        f"Auto-queuing reconcile in {heal_delay}s."
                    )
                    import threading as _threading
                    _threading.Timer(heal_delay, lambda: action_trigger("reconcile", {})).start()

                break  # Exhausted retries.
