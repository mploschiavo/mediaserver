"""HTTP API serve mode for the bootstrap controller.

ADR-0005 Phase 5c.4 closure (partial — see report)
--------------------------------------------------

The legacy subprocess-per-action machinery (`_MP_CTX`, `_action_worker`,
`_SubprocessState`) has been retired. Every action now runs through
`_dispatch_action` -> `JobRunner.run` on a single background worker
thread inside the controller process. The thread is started once at
boot and consumes the same priority queue the previous loop drained.

What stayed and why
~~~~~~~~~~~~~~~~~~~

* `state.start_action`/`finish_action`/`cancel_action`/`add_pending`/
  `pop_pending` remain on `ControllerState`. They feed log-line
  action tagging (`state.append_log` reads `current_action.name`),
  the `/status` `action_history` array consumed by the k8s wait
  service in `cli/workflows/controller_job_wait_service.py`, the
  RSS feed in `metrics.py::get_rss_feed`, and the running-rows
  branch of `jobs.py::_RunningJobsAggregator`. Removing them per
  the original 5c.4 spec requires a coordinated rewrite of those
  consumers; that is deferred to a follow-up commit.

* The local `action_trigger` closure remains because every config
  writer in `routes/post_*` and the `auth_config` service depends on
  it as the queue-injection seam. It now enqueues onto the same
  in-process priority queue the worker thread drains.

What changed
~~~~~~~~~~~~

* No more `multiprocessing.spawn`: the worker is a daemon thread.
  The fork-deadlock guard (`_MP_CTX = get_context("spawn")`) is
  gone with it.
* No more inter-process log queue: the thread shares
  `runtime_platform.log` directly, so action log lines arrive
  with the same `current_action` tag they always did (no log-tag
  regression vs. the subprocess shape).
* Cancellation: `state.is_cancelled` -> `framework.request_cancel()`
  signals the in-process JobRunner via the existing module-global
  flag the SIGTERM handler used to set. No process kill.
* Timeout: a watchdog daemon thread per dispatched action calls
  `framework.request_cancel()` if the worker exceeds the budget.
  The action loop then completes through the next `JobContext.
  check_cancelled()` instead of being SIGKILL'd.
"""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import argparse
import os
import queue
import threading
from pathlib import Path as _Path

import yaml as _yaml

import media_stack.services.runtime_platform as runtime_platform
from media_stack.core.auth.configure_auth_job import (
    configure_auth as _CONFIGURE_AUTH_FN,
)

from media_stack.cli.commands.controller_dispatch import (
    _dispatch_action,
)
from media_stack.services.jobs.controller_handlers import (
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
    except Exception as exc:
        # Service not ready yet — skip validation.
        log_swallowed(exc)


class _BootCtxShim:
    """Minimal ctx object for the configure-auth job — same attributes
    the dispatcher would assemble, without pulling in the whole
    action pipeline just to run one sync step at boot."""

    def __init__(self, profile: dict, config_root: str, admin_username: str) -> None:
        self.profile = profile
        self.config_root = config_root
        self.admin_username = admin_username


def _run_boot_configure_auth(state: object) -> None:
    """Write the Authelia config before the API server opens.

    When profile.auth.provider is authelia(+oidc), the Authelia
    container is waiting on the controller's health endpoint and
    will start immediately once it returns 200. If it reads
    placeholder secrets from the bootstrap defaults it encrypts
    db.sqlite3 with those placeholders, and the real secrets that
    configure-auth later emits become unable to decrypt the data.
    Running configure-auth here makes the first Authelia boot use
    real secrets on its very first write, closing that window."""
    del state  # reserved for future hooks; not needed to read profile
    env = dict(os.environ)
    try:
        profile = _load_boot_profile(env)
        auth_cfg = profile.get("auth") or {}
        provider = str(auth_cfg.get("provider", "") or "").strip().lower()
        if provider not in ("authelia", "authelia+oidc"):
            return
        ctx = _BootCtxShim(
            profile=profile,
            config_root=env.get("CONFIG_ROOT", "/srv-config"),
            admin_username=env.get("STACK_ADMIN_USERNAME", "admin"),
        )
        result = _CONFIGURE_AUTH_FN(ctx)
        if result.get("error"):
            runtime_platform.log(
                f"[WARN] boot configure-auth: {result['error']}",
            )
        else:
            runtime_platform.log(
                "[OK] boot configure-auth: Authelia config "
                "sealed before API server opened",
            )
    except Exception as exc:  # noqa: BLE001
        runtime_platform.log(
            f"[WARN] boot configure-auth raised: {exc}",
        )


def _load_boot_profile(env: dict) -> dict:
    """Best-effort profile load for the boot configure-auth step.

    Reads BOOTSTRAP_PROFILE_FILE from the passed env dict so the
    env is sampled exactly once at boot and the method stays off
    os.environ (the class-structure ratchet)."""
    pf = str(env.get("BOOTSTRAP_PROFILE_FILE", "")).strip()
    if not pf:
        return {}
    path = _Path(pf)
    if not path.is_file():
        return {}
    try:
        data = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        runtime_platform.log(
            f"[DEBUG] boot configure-auth: profile load failed: {exc}",
        )
        return {}
    return data if isinstance(data, dict) else {}


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
            from media_stack.services.jobs.controller_handlers import _auto_generate_config_json
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
        # Plumb the resolved value into ``os.environ`` so downstream
        # probes/ensurers that fall through to ``os.environ.get("CONFIG_ROOT")``
        # — notably ``adapters/jellyseerr/config_wiring.py::_settings_path`` —
        # see the same value the CLI was given. Compose passes
        # ``--config-root /srv-config`` as a flag (no env), and pre-Phase-E
        # the legacy chain compensated for the gap; post-cleanup the
        # probe path is the only consumer and was reading "" forever.
        os.environ["CONFIG_ROOT"] = config_root
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

    # Run configure-auth SYNCHRONOUSLY before the API server comes up.
    # The Authelia container is waiting on the controller's health
    # probe; it must not start with a placeholder encryption_key
    # because Authelia would encrypt db.sqlite3 with that placeholder,
    # and when configure-auth later swapped it for a real key the
    # existing rows would become undecryptable (the recurring
    # crashloop we kept hitting). Fail-open: if anything here goes
    # wrong we log and proceed — the API server still starts and
    # configure-auth will be retried on the first bootstrap action.
    _run_boot_configure_auth(state)

    port = int(args.api_port or os.environ.get("BOOTSTRAP_API_PORT", "9100"))
    action_queue: queue.PriorityQueue[tuple[int, int, str, dict]] = queue.PriorityQueue()
    # 1800s = 30 min. discover-indexers alone runs ~14 min on a
    # fresh install (per-indexer Cardigann probes against ~70
    # indexers, 8-way parallel). Plus jellyfin preflight + library
    # configure + post-setup. The previous 600s default killed the
    # bootstrap mid-DAG every time. Override via env if you have
    # a slower link or more indexers.
    action_timeout = int(os.environ.get("BOOTSTRAP_ACTION_TIMEOUT", "1800"))
    max_retries = int(os.environ.get("BOOTSTRAP_ACTION_MAX_RETRIES", "0"))
    _queue_seq = 0
    _queue_seq_lock = threading.Lock()

    def action_trigger(action_name: str, overrides: dict) -> None:
        nonlocal _queue_seq
        from media_stack.api.server import ACTION_PRIORITY, DEFAULT_ACTION_PRIORITY
        prio = int(overrides.pop("_priority", ACTION_PRIORITY.get(action_name, DEFAULT_ACTION_PRIORITY)))
        with _queue_seq_lock:
            _queue_seq += 1
            seq = _queue_seq
        action_queue.put((prio, seq, action_name, overrides))
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

    # ------------------------------------------------------------------
    # Media-integrity subsystem: enforce the canonical *arr + Bazarr
    # policy at boot, reconcile duplicates every 15 min. See
    # ``docs/media-integrity.md`` for the full contract. Wiring is
    # best-effort: a missing policy file, unreachable adapter, or
    # unset API-key env var logs a warning and continues — we never
    # want the media-integrity daemon to block the controller from
    # serving its core surface.
    # ------------------------------------------------------------------
    # Build the media-integrity service singleton. Cadence (scan /
    # reconcile / enforce-config) is now driven by the JobRunner via
    # contract-registered jobs in
    # ``contracts/services/media_integrity.yaml``; the legacy
    # daemon-thread scheduler was removed in v1.0.184 so history flows
    # into ``GET /api/jobs.history[]`` and actor tagging works for
    # manual triggers. The scheduler service below seeds the three
    # cron-driven media-integrity entries.
    try:
        from media_stack.api.services.media_integrity_handlers import (
            _instance as _media_integrity_api,
        )
        from media_stack.services.media_integrity.factory import (
            build_default_service as _build_media_integrity,
        )

        _mi_service = _build_media_integrity()
        _media_integrity_api.set_service(_mi_service)
        runtime_platform.log(
            "[INFO] Media integrity: service ready "
            "(driven by JobRunner; legacy scheduler removed)"
        )
    except FileNotFoundError as exc:
        # Policy file missing — non-fatal; the feature just isn't live.
        runtime_platform.log(
            f"[WARN] Media integrity: policy contract missing "
            f"({exc}); subsystem disabled"
        )
    except Exception as exc:  # noqa: BLE001 — defensive; must not block boot
        runtime_platform.log(
            f"[WARN] Media integrity: init failed ({exc}); subsystem disabled"
        )

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

    # ------------------------------------------------------------------
    # Scheduler dispatch loop — fires recurring actions (e.g. hourly
    # media-hygiene). Seeds default schedules on first start so the
    # compose deploy gets the same automatic cleanup the k8s
    # CronJobs already provide. Without this loop, actions added via
    # SchedulerService.add_schedule() were stored to disk but never
    # actually fired (the loop existed in spec but had no caller).
    # The end goal is that users never need to log into qBit/*arr/etc.
    # to clean up; the controller maintains the queue itself.
    # ------------------------------------------------------------------
    def _scheduler_loop() -> None:
        import time as _t
        from media_stack.api.services import scheduler as _sched
        # Seed defaults on first start (idempotent — checks for an
        # existing entry before adding).
        try:
            existing = {s.get("action") for s in _sched.get_schedules().get("schedules") or []}
            # Hourly stalled/orphan torrent cleanup. The aggressive
            # defaults in _guardrail_config.py only apply when a run
            # actually fires; without this seed, nothing fires on
            # compose.
            if "run-media-hygiene" not in existing:
                _sched.add_schedule(
                    action="run-media-hygiene",
                    interval_seconds=3600,  # 1h — was 6h on k8s CronJob
                    label="Auto-cleanup stalled / orphaned downloads (hourly)",
                )
                runtime_platform.log(
                    "[INFO] Scheduler: seeded default 'run-media-hygiene' "
                    "(every 1h)"
                )
            # Periodic scan of completed-downloads paths so files the
            # user dropped into qBit directly (or that the *arr missed
            # via webhook) get picked up. Each *arr has a
            # ``DownloadedMoviesScan`` / ``DownloadedEpisodesScan``
            # command that walks its configured download path and
            # imports anything it recognizes by metadata. Without this
            # job, manually-added qBit content never reaches the media
            # library. (v1.0.144.)
            if "scan-completed-downloads" not in existing:
                _sched.add_schedule(
                    action="scan-completed-downloads",
                    interval_seconds=900,  # 15min
                    label="Scan completed downloads into *arr libraries (15m)",
                )
                runtime_platform.log(
                    "[INFO] Scheduler: seeded default "
                    "'scan-completed-downloads' (every 15m)"
                )
            # Hourly heartbeat for mass-search-throttled. The adapter
            # itself is adaptive — when the library is "thin" or qBit
            # has been idle, it runs aggressively (every tick); when
            # the library is healthy AND qBit is busy downloading, it
            # short-circuits as a no-op. Self-throttling: empty
            # installs get hammered with searches so first-hour
            # impressions land fast; healthy installs run lazily.
            # (v1.0.148.)
            if "mass-search-throttled" not in existing:
                _sched.add_schedule(
                    action="mass-search-throttled",
                    interval_seconds=3600,  # 1h heartbeat; adapter decides
                    label="Adaptive search for missing content (hourly)",
                )
                runtime_platform.log(
                    "[INFO] Scheduler: seeded default "
                    "'mass-search-throttled' (every 1h, adaptive)"
                )
            # Catches the "qBit-completed but *arr never imported"
            # failure mode (Shelter + Strangers incidents, v1.0.150).
            # Two paths: queue entries stuck "downloading" forever,
            # AND orphan files in /data/torrents/completed/ that the
            # *arr's queue doesn't know about. Both get force-imported
            # via /api/v3/manualimport.
            if "recover-stuck-imports" not in existing:
                _sched.add_schedule(
                    action="recover-stuck-imports",
                    interval_seconds=1800,  # 30min
                    label="Recover stuck/orphan downloads (every 30m)",
                )
                runtime_platform.log(
                    "[INFO] Scheduler: seeded default "
                    "'recover-stuck-imports' (every 30m)"
                )
            # ----------------------------------------------------------
            # Media-integrity jobs (v1.0.184). Replaces the legacy
            # in-process daemon-thread scheduler. Each cadence maps to
            # a contract-registered job; manual SPA triggers reach
            # the same job through ``handlers_post`` so history is
            # unified in /api/jobs.history[].
            #
            # Cron-equivalence:
            #   media-integrity:scan          → */15 * * * *  (15 min)
            #   media-integrity:reconcile     → 0 */6 * * *   (6 h)
            #   media-integrity:enforce-config→ 0 4 * * *     (24 h)
            # The interval scheduler doesn't support clock-aligned
            # cron expressions; cadence-equivalence is the contract.
            # ----------------------------------------------------------
            if "media-integrity:scan" not in existing:
                _sched.add_schedule(
                    action="media-integrity:scan",
                    interval_seconds=900,  # 15min
                    label="Media-integrity status scan (every 15m)",
                )
                runtime_platform.log(
                    "[INFO] Scheduler: seeded default "
                    "'media-integrity:scan' (every 15m)"
                )
            if "media-integrity:reconcile" not in existing:
                _sched.add_schedule(
                    action="media-integrity:reconcile",
                    interval_seconds=21600,  # 6h
                    label="Media-integrity duplicate reconcile (every 6h)",
                )
                runtime_platform.log(
                    "[INFO] Scheduler: seeded default "
                    "'media-integrity:reconcile' (every 6h)"
                )
            if "media-integrity:enforce-config" not in existing:
                _sched.add_schedule(
                    action="media-integrity:enforce-config",
                    interval_seconds=86400,  # 24h
                    label="Media-integrity policy enforcement (daily)",
                )
                runtime_platform.log(
                    "[INFO] Scheduler: seeded default "
                    "'media-integrity:enforce-config' (every 24h)"
                )
        except Exception as exc:
            runtime_platform.log(
                f"[WARN] Scheduler seed failed: {exc}"
            )
        # Tick loop. Wakes every 60s, fires anything due. Keeps the
        # cadence simple — finer granularity than 1m is overkill for
        # cleanup work.
        _t.sleep(120)  # Let bootstrap finish before first tick
        while True:
            try:
                due = _sched.get_due_actions()
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
                    except Exception as exc:
                        runtime_platform.log(
                            f"[WARN] Scheduler dispatch '{action}' failed: {exc}"
                        )
            except Exception as exc:
                runtime_platform.log(
                    f"[WARN] Scheduler tick failed: {exc}"
                )
            _t.sleep(60)
    sched_thread = threading.Thread(
        target=_scheduler_loop, daemon=True, name="scheduler-dispatch",
    )
    sched_thread.start()

    auto_run = args.auto_run or os.environ.get("FULLY_PRECONFIGURED") == "1"
    if auto_run:
        runtime_platform.log("[INFO] Auto-run: queuing initial bootstrap action")
        action_trigger("bootstrap", {})

    # -----------------------------------------------------------------------
    # Action worker — runs on a daemon THREAD (not a subprocess).
    #
    # ADR-0005 Phase 5c.4: replaced the multiprocessing-spawn worker with
    # in-process dispatch. ``_dispatch_action`` already routes through
    # ``run_job`` -> ``JobRunner.run`` which records its own per-job and
    # batch run history; the parent thread's only job is to drain the
    # priority queue, enforce a per-action timeout via the framework's
    # cooperative-cancel signal, and run the post-action auto-heal /
    # webhook hooks the legacy loop owned.
    #
    # Cancellation: ``state.is_cancelled`` (set by POST /cancel via
    # ``state.cancel_action()``) plumbs through to
    # ``framework.request_cancel()`` so the in-flight ``JobContext``
    # raises ``CancelledError`` at its next ``check_cancelled()`` call.
    # Same semantics the SIGTERM handler in the deleted ``_action_worker``
    # used to give us — minus the hard-kill, which the in-process model
    # cannot offer (and operators haven't relied on for the real
    # contract-driven jobs since they all check cancel cooperatively).
    # -----------------------------------------------------------------------

    def _run_one_action(action_name: str, overrides: dict, action_record) -> str | None:
        """Run a single action synchronously. Returns error message or None on success.

        The call goes through ``_dispatch_action`` -> ``run_job`` ->
        ``JobRunner.run``. A watchdog daemon thread enforces the per-
        action timeout by setting ``framework.request_cancel()`` if
        the action exceeds its budget; the JobRunner notices on its
        next prereq/job boundary.
        """
        from media_stack.services.jobs import framework as _fw

        timeout_seconds = max(1, int(overrides.get("timeout") or action_timeout))
        cancel_event = threading.Event()
        timed_out = threading.Event()

        def _watchdog() -> None:
            # Heartbeat every 60s; trip cancel when the budget is gone.
            import time as _t
            t0 = _t.monotonic()
            next_heartbeat = t0 + 60
            while not cancel_event.wait(timeout=1.0):
                now = _t.monotonic()
                elapsed = now - t0
                if elapsed >= timeout_seconds:
                    timed_out.set()
                    runtime_platform.log(
                        f"[ACTION] {action_name}: TIMED OUT after "
                        f"{elapsed:.0f}s (limit {timeout_seconds}s) — "
                        "requesting cooperative cancel"
                    )
                    _fw.request_cancel()
                    return
                if now >= next_heartbeat:
                    next_heartbeat = now + 60
                    runtime_platform.log(
                        f"[ACTION] {action_name}: still running "
                        f"({elapsed:.0f}s elapsed, timeout {timeout_seconds}s)"
                    )
                # Also propagate operator-initiated cancel from the
                # ControllerState into the framework's module-global
                # cancel flag.
                if state.is_cancelled:
                    _fw.request_cancel()

        watchdog = threading.Thread(
            target=_watchdog, daemon=True, name=f"action-watchdog-{action_record.id}",
        )
        watchdog.start()

        # Reset the framework's module-global cancel flag for this run;
        # it persists across calls otherwise.
        _fw.clear_cancel()

        error_msg: str | None = None
        try:
            _dispatch_action(action_name, overrides, args, state)
        except Exception as exc:  # noqa: BLE001
            error_msg = str(exc)
        finally:
            cancel_event.set()
            watchdog.join(timeout=2)

        if timed_out.is_set() and not error_msg:
            error_msg = f"timed out after {timeout_seconds}s"
        if state.is_cancelled and not error_msg:
            error_msg = "cancelled by user"

        # Reset the cancel flag so the next action starts clean.
        _fw.clear_cancel()
        return error_msg

    def _action_loop() -> None:
        """Drain the action queue forever. Runs on a daemon thread."""
        while True:
            try:
                _prio, _seq, action_name, overrides = action_queue.get()
            except KeyboardInterrupt:
                runtime_platform.log("[INFO] Shutting down bootstrap service")
                server.shutdown()
                return

            state.pop_pending(action_name)

            # Merge runtime_config into overrides so toggles like
            # auto_download_content propagate to ``_apply_overrides``.
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

                error_msg = _run_one_action(action_name, dict(overrides), action_record)
                cancelled = error_msg == "cancelled by user"

                if cancelled:
                    state.finish_action(error="cancelled by user")
                    runtime_platform.log(f"[ACTION] {action_name}: cancelled")
                    break

                if error_msg:
                    state.finish_action(error=error_msg)
                    runtime_platform.log(f"[ERR] Action {action_name} failed: {error_msg}")

                    if action_name == "bootstrap" and not state.initial_bootstrap_done:
                        state.mark_initial_bootstrap_done()
                        runtime_platform.log(
                            "[WARN] Initial bootstrap had errors but service is marked ready"
                        )
                        for queued in ["configure-media-server", "post-setup", "envoy-config", "validate-credentials"]:
                            runtime_platform.log(f"[INFO] Auto-queuing {queued} despite bootstrap error")
                            # Tag as auto-heal so the dashboard badges
                            # the recovery cascade correctly.
                            action_trigger(queued, {"_source": "auto-heal"})

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
                        # Tag the auto-queued reconcile as auto-heal so
                        # the history badge reads ``auto-heal`` instead
                        # of ``unknown`` — operators need to distinguish
                        # "the controller decided to retry" from "the
                        # cron schedule fired".
                        threading.Timer(
                            heal_delay,
                            lambda: action_trigger(
                                "reconcile", {"_source": "auto-heal"},
                            ),
                        ).start()

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
                        state.mark_initial_bootstrap_done()
                        runtime_platform.log("[INFO] Initial bootstrap complete — service is ready")
                        # Historical: this used to auto-queue
                        # ``configure-media-server / post-setup / envoy-config /
                        # discover-indexers / validate-credentials`` because
                        # the original ``bootstrap`` was a thin wrapper that
                        # didn't run them. Bootstrap is now the full DAG (see
                        # contracts/services/core.yaml::bootstrap-orchestrate),
                        # so re-queuing those wastes ~14 min on a second
                        # discover-indexers pass and does nothing else useful.
                        # If a job needs to run AFTER bootstrap, add it as a
                        # downstream contract job — don't bring this back.

                    break  # Success — exit retry loop.

    action_thread = threading.Thread(
        target=_action_loop, daemon=True, name="action-dispatch",
    )
    action_thread.start()

    # Park the main thread on the API server. The action thread is a
    # daemon so it dies with us. Previously the dispatch loop ran on
    # the main thread; now it lives on its own thread so KeyboardInterrupt
    # in the API thread bubbles up here cleanly.
    try:
        action_thread.join()
    except KeyboardInterrupt:
        runtime_platform.log("[INFO] Shutting down bootstrap service")
        server.shutdown()
