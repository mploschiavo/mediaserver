"""Small, independently testable bootstrap jobs.

Each job is a self-contained configuration step that:
- Can be run independently via POST /actions/{job-name}
- Has clear pre-conditions (what must be true before it runs)
- Has clear post-conditions (what is true after it succeeds)
- Doesn't depend on the config JSON adapter_hooks chain
- Can be composed into larger jobs (bootstrap = all jobs)

A job can contain sub-jobs (job has jobs pattern).
"""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import importlib
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

import yaml

import media_stack.services.runtime_platform as runtime_platform
import logging

# ``Job``, ``CancelledError``, ``_noop``, ``PREREQS``, ``register_prereq``
# and the history-schema constants live in the domain layer (pure
# value objects, no I/O). Re-exported at module scope so existing
# call-sites that do ``from media_stack.services.jobs.framework
# import Job`` keep working through the legacy shim.
from media_stack.domain.jobs.types import (
    CancelledError,
    Job,
    PREREQS,
    _HISTORY_SOURCE_VALUES,
    _JOB_HISTORY_MAX,
    _noop,
    _normalize_source,
    register_prereq,
)

# Imported at module top to keep ``_apply_contract_lifecycle_metadata``
# free of a function-level import (which would tick the
# CIRCULAR_IMPORT_RISK_RATCHET). The lifecycle handler module imports
# only from sibling ``application/jobs/`` modules, so there's no
# cycle in the static graph — the import is safe.
from media_stack.application.jobs.job_lifecycle_metadata import (
    JobLifecycleMetadataHandler as _JobLifecycleMetadataHandler,
)


# ---------------------------------------------------------------------------
# Config loading from service contracts
# ---------------------------------------------------------------------------

def _find_contracts_dir() -> Path | None:
    """Locate the contracts/services/ YAML directory."""
    # Single env read — the previous ``X if X else None`` pattern read
    # SERVICES_REGISTRY_DIR twice, doubling the os.environ count without
    # adding semantic value.
    _override = os.environ.get("SERVICES_REGISTRY_DIR", "")
    candidates = [
        Path(_override) if _override else None,
        Path("/opt/media-stack/contracts/services"),
        Path(__file__).resolve().parents[4] / "contracts" / "services",
        Path("contracts/services"),
    ]
    for p in candidates:
        if p and p.is_dir() and any(p.glob("*.yaml")):
            return p
    return None


def _load_cfg_from_contracts(profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a flat config dict from per-service YAML contracts.

    Services with all-complex defaults (e.g., jellyfin whose defaults are
    all dicts/lists like libraries, livetv, plugins) are flattened:
        jellyfin.defaults.libraries → cfg["jellyfin_libraries"]

    Services with mixed types (e.g., bazarr has enabled, url as scalars)
    keep their service-id form: cfg["bazarr"]

    Technology bindings are derived from service capabilities when not
    provided by the profile (e.g., jellyfin declares media_server: true).
    """
    cfg: dict[str, Any] = {}

    # Technology bindings + app auth from profile
    if profile:
        for key in ("technology_bindings", "app_auth"):
            if key in profile:
                cfg[key] = profile[key]

    svc_dir = _find_contracts_dir()
    if not svc_dir:
        return cfg

    # Capability → technology_bindings role mapping
    _CAPABILITY_TO_ROLE = {
        "media_server": "media_server",
        "torrent_client": "torrent_client",
        "usenet_client": "usenet_client",
        "request_manager": "request_manager",
        "indexer_manager": "indexer_manager",
    }
    derived_bindings: dict[str, str] = {}

    for svc_yaml in sorted(svc_dir.glob("*.yaml")):
        if svc_yaml.name.startswith("_"):
            continue
        try:
            svc_data = yaml.safe_load(svc_yaml.read_text(encoding="utf-8")) or {}
            svc_id = svc_data.get("service", {}).get("id", "")
            if not svc_id:
                continue

            # Derive technology bindings from capabilities
            capabilities = svc_data.get("plugin", {}).get("capabilities", {})
            for cap_key, role in _CAPABILITY_TO_ROLE.items():
                if capabilities.get(cap_key):
                    derived_bindings.setdefault(role, svc_id)

            defaults = svc_data.get("defaults", {})
            if not defaults:
                continue
            all_complex = all(isinstance(v, (dict, list)) for v in defaults.values())
            if all_complex:
                for sub_key, sub_val in defaults.items():
                    cfg[f"{svc_id}_{sub_key}"] = sub_val
            else:
                cfg[svc_id] = defaults
        except Exception as exc:
            runtime_platform.log(f"[DEBUG] Failed to load contract {svc_yaml.name}: {exc}")
            continue

    # Fill in technology_bindings from service capabilities if not in profile
    if "technology_bindings" not in cfg and derived_bindings:
        cfg["technology_bindings"] = derived_bindings
    elif "technology_bindings" in cfg:
        # Merge: profile takes precedence, derived fills gaps
        for role, svc_id in derived_bindings.items():
            cfg["technology_bindings"].setdefault(role, svc_id)

    # Apply per-app config overrides (from {service}/controller.yaml),
    # then fall back to profile overrides. Per-app config wins over profile.
    from media_stack.services.app_config_service import load_app_config

    # Media server overrides: per-app config → profile fallback
    ms_id = cfg.get("technology_bindings", {}).get("media_server", "")
    if ms_id:
        ms_app = load_app_config(ms_id)
        # Livetv override
        livetv_override = ms_app.get("livetv", {})
        if not livetv_override and profile:
            livetv_override = profile.get("live_tv_defaults", {})
        livetv_key = f"{ms_id}_livetv"
        if livetv_override and livetv_key in cfg:
            target = cfg[livetv_key]
            for k, v in livetv_override.items():
                if v is not None:
                    target[k] = v
        # Libraries override
        lib_override = ms_app.get("libraries")
        if not lib_override and profile:
            ms_prof = profile.get(ms_id, {})
            if isinstance(ms_prof, dict):
                lib_override = ms_prof.get("libraries")
        lib_key = f"{ms_id}_libraries"
        if lib_override and lib_key in cfg:
            cfg[lib_key]["libraries"] = lib_override

    # Enrich tuners/guides with required handler fields
    from media_stack.services.livetv_config_service import enrich_livetv_entries
    enrich_livetv_entries(cfg, profile or {})

    return cfg


# Re-export for backward compatibility with tests
from media_stack.services.livetv_config_service import (  # noqa: E402,F811
    _url_looks_valid as _url_looks_valid,
    extract_country_code as _extract_country_code,
    enrich_livetv_entries as _enrich_livetv_entries_impl,
)


# ---------------------------------------------------------------------------
# Job execution history — last N runs with per-job timing
# ---------------------------------------------------------------------------


def _history_file() -> Path:
    config_root = os.environ.get("CONFIG_ROOT", "/srv-config")
    return Path(config_root) / ".controller" / "job-history.json"


def get_job_history() -> list[dict[str, Any]]:
    """Return recent job execution history (newest first). Reads from disk.

    Pre-existing entries on disk that lack a ``source`` field are
    backfilled with ``"unknown"`` here so the UI never has to
    handle a missing key — old serialized entries from before the
    field existed remain readable.
    """
    path = _history_file()
    if not path.is_file():
        return []
    try:
        import json
        entries = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            return []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry.setdefault("source", "unknown")
            entry.setdefault("actor", None)
        return list(reversed(entries))
    except Exception:
        return []


def _terminal_status(result: dict[str, Any] | None) -> str:
    """Map a framework result dict to a ``RunStatus`` terminal
    string. The framework uses ``"running_in_background"`` for in-
    flight async jobs and ``"prereq_not_met"`` for blocked-by-
    prereq skips; both collapse to the appropriate terminal value
    in the run-history record."""
    from media_stack.domain.jobs.run_record import RunStatus
    if not result:
        return RunStatus.OK
    raw = str(result.get("status") or "").lower()
    if raw in {"ok", "complete"}:
        return RunStatus.OK
    if raw in {"error", "errors", "failed"}:
        return RunStatus.ERROR
    if raw in {"skipped", "prereq_not_met"}:
        return RunStatus.SKIPPED
    if raw == "cancelled":
        return RunStatus.CANCELLED
    if raw in {"timeout", "timed_out"}:
        return RunStatus.TIMEOUT
    return RunStatus.OK


def _iso_at(epoch_seconds: float) -> str:
    """ISO-8601 UTC timestamp for the run-history log-anchor field."""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(
        epoch_seconds, tz=timezone.utc,
    ).isoformat()


def _build_per_job_history_record(r: dict[str, Any]) -> dict[str, Any]:
    """Trim a per-job framework result down to the fields the
    dashboard's Jobs UI surfaces, preserving the operator-debugging
    payload (``error`` text, ``skip_reason`` for "why was this
    skipped?" tooltips, ``attempts`` for retry counters). Pre-v1.0.270
    this dropped ``error`` entirely — operators saw a red status chip
    with no way to read the failure short of ssh + ``cat
    job-history.json``."""
    out: dict[str, Any] = {
        "status": r.get("status", "?"),
        "elapsed": r.get("elapsed", 0),
    }
    err = r.get("error")
    if err:
        # The framework already truncates exception strings to 200
        # chars at the writer site; truncate here too as a
        # belt-and-suspenders cap so a misbehaving handler can't
        # bloat history.json.
        out["error"] = str(err)[:500]
    skip_reason = r.get("skip_reason") or r.get("skipped_reason")
    if skip_reason:
        out["skip_reason"] = str(skip_reason)[:200]
    attempts = r.get("attempts") or r.get("attempt_count")
    if isinstance(attempts, int) and attempts > 1:
        out["attempts"] = attempts
    return out


def _record_history(
    result: dict[str, Any],
    *,
    source: str | None = None,
    actor: str | None = None,
) -> None:
    """Record a job run result in history. Writes to disk (survives subprocess).

    ``source`` tags who triggered the run (``"cron"``, ``"manual"``,
    ``"auto-heal"``, ``"scheduler"``, ``"unknown"``); sub-tagging
    like ``"cron:reconcile"`` is preserved. ``actor`` carries the
    authenticated username for ``manual`` runs (``None``
    otherwise). Defaults keep the function backwards-compatible
    with callers that don't yet thread a source through.
    """
    import json
    entry = {
        "ts": time.time(),
        "elapsed": result.get("elapsed", 0),
        "ok": result.get("ok", 0),
        "skipped": result.get("skipped", 0),
        "errors": result.get("errors", 0),
        "source": _normalize_source(source),
        "actor": (str(actor).strip() or None) if actor else None,
        "jobs": {
            name: _build_per_job_history_record(r)
            for name, r in result.get("jobs", {}).items()
        },
    }
    path = _history_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        if not isinstance(existing, list):
            existing = []
        existing.append(entry)
        if len(existing) > _JOB_HISTORY_MAX:
            existing = existing[-_JOB_HISTORY_MAX:]
        path.write_text(json.dumps(existing), encoding="utf-8")
    except Exception as exc:
        log_swallowed(exc)


# ---------------------------------------------------------------------------
# Job framework — prerequisite-based DAG dispatcher
#
# The framework is generic. Job definitions, prerequisites, and the tree
# structure are all pluggable. Delete "bootstrap" tomorrow and create
# "my-new-workflow" — the framework doesn't change.
#
# - Job: a unit of work with optional prereqs and sub-jobs (N-level)
# - PREREQS: named condition registry (pluggable, not hardcoded to any service)
# - JobRunner: waits for prereqs with active retry, then executes the tree
# ---------------------------------------------------------------------------


class JobRunner:
    """Event-driven job dispatcher — no sleep, no polling.

    Flattens the job tree, then runs in rounds:
    1. Check which jobs have all prereqs met → run them
    2. After each job completes, re-evaluate deferred jobs
    3. If a round produces no progress and deferred jobs remain,
       try to satisfy prereqs actively (e.g., run preflight)
    4. Repeat until all jobs are done or no more progress is possible

    No sleep loops. No polling. Each round makes progress or stops.
    """

    def __init__(
        self,
        root: Job,
        ctx: "JobContext",
        max_attempts: int = 3,
        *,
        source: str | None = None,
        actor: str | None = None,
        **kwargs: Any,
    ):
        # ADR-0005 Phase 5c.2: ``max_attempts`` (and the legacy
        # ``max_wait`` kwarg alias) are accepted for call-site
        # compatibility but no longer wire up a retry-the-whole-loop
        # tier on JobRunner. Per-job retry is the
        # ``Job.max_attempts`` contract field, threaded into the
        # per-job dispatch site further down (line ~1225).
        del max_attempts
        kwargs.pop("max_wait", None)
        self.root = root
        self.ctx = ctx
        # ``source`` / ``actor`` flow into ``_record_history`` so
        # ``GET /api/jobs.history[]`` carries a who-triggered-this
        # tag. ``None`` defaults to ``"unknown"`` at the writer
        # site — keeps existing JobRunner(...) call-sites working
        # without forcing every caller to opt in.
        self.source: str | None = source
        self.actor: str | None = actor
        # ``dispatched``: jobs we've started (sync ran, or async spawned).
        #   Prevents re-dispatch.
        # ``done``: jobs whose handler has FULLY FINISHED (sync handlers
        #   are added immediately; non_blocking handlers add themselves
        #   from the daemon thread on completion).
        # Downstream ordering uses ``done``, not ``dispatched`` — that's
        # the whole point of the ``after:`` field.
        self.dispatched: set[str] = set()
        self.done: set[str] = set()
        self.results: dict[str, dict[str, Any]] = {}
        # Condition variable so the dispatch loop can sleep when stuck
        # (jobs in flight, none yet ready) and wake instantly when an
        # async daemon thread finishes — no polling, no fixed sleeps.
        self._cv = threading.Condition()

    def _ready(self, job: Job, all_job_names: set[str]) -> bool:
        """A job is ready when (a) prereqs met AND (b) every job named
        in its ``after`` field has fully completed. ``after`` entries
        that don't match any known job name are treated as already-
        satisfied so a typo doesn't permanently wedge the runner."""
        if job.check_prereqs(self.ctx) is not None:
            return False
        for dep in getattr(job, "after", []):
            if dep in all_job_names and dep not in self.done:
                return False
        return True

    def _async_jobs_running(self) -> bool:
        """True when at least one dispatched non_blocking job is still
        in its daemon thread (dispatched but not yet in ``done``)."""
        return bool(self.dispatched - self.done)

    def run(self) -> dict[str, Any]:
        """Execute all jobs in dependency order."""
        t0 = time.time()
        all_jobs = self._flatten(self.root)
        all_job_names = {j.name for j in all_jobs}
        runtime_platform.log(f"[INFO] JobRunner: {len(all_jobs)} jobs to dispatch")

        # Record a parent ``batch`` run that owns every job in this
        # batch. The per-job run records below carry batch_id =
        # batch.run_id + parent_run_id = batch.run_id, so the Jobs
        # UI can render the full tree.
        from media_stack.application.jobs.run_history import (
            record_run_start as _rh_start,
            record_run_complete as _rh_complete,
        )
        from media_stack.domain.jobs.run_record import RunStatus as _RS
        try:
            batch_run = _rh_start(
                getattr(self.root, "name", "batch"),
                triggered_by=self.source or "unknown",
                actor=self.actor,
            )
            batch_id_for_children: str | None = batch_run.run_id
        except Exception as exc:  # noqa: BLE001
            log_swallowed(exc)
            batch_run = None
            batch_id_for_children = None

        # ADR-0005 Phase 5c.2: dropped the bootstrap-only
        # retry-on-no-ready-jobs shape. Pre-Phase-5 the loop would
        # call ``_try_satisfy_prereqs`` (now retired) and re-enter
        # the dispatch up to ``max_attempts`` times. Post-Phase-5c.1
        # every API-key-discoverable promise routes through the
        # orchestrator's lifecycle dispatch — itself a settle loop —
        # so JobRunner doesn't need its own retry tier. When nothing
        # is ready and no async work is in flight, mark the blocked
        # jobs ``prereq_not_met`` and break cleanly. ``Job.max_attempts``
        # (the contract field) still threads per-job retry tunables
        # through ``check_prereqs`` and the per-job dispatch sites
        # further down.
        while True:
            pending = [j for j in all_jobs if j.name not in self.dispatched]
            if not pending:
                # Everything is dispatched. If async jobs are still in
                # flight, wait for them to finish before declaring done.
                if self._async_jobs_running():
                    with self._cv:
                        self._cv.wait(timeout=1.0)
                    continue
                break

            ready = [j for j in pending if self._ready(j, all_job_names)]
            blocked = [j for j in pending if not self._ready(j, all_job_names)]

            if not ready:
                # Nothing ready. If async jobs are running, their
                # completion may unblock something — wait on the CV.
                if self._async_jobs_running():
                    with self._cv:
                        self._cv.wait(timeout=2.0)
                    continue
                # No async work in flight either — the orchestrator's
                # promise loop is the canonical "satisfy missing
                # prereqs" path; nothing JobRunner can do here. Mark
                # the blocked jobs and break.
                for j in blocked:
                    reason = (
                        j.check_prereqs(self.ctx)
                        or f"after-deps not done: {[d for d in j.after if d in all_job_names and d not in self.done]}"
                    )
                    runtime_platform.log(f"[WARN] {j.name}: deferred — {reason}")
                    self.results[j.name] = {"status": "prereq_not_met", "reason": reason}
                    self.dispatched.add(j.name)
                    self.done.add(j.name)
                break

            for job in ready:
                if self.ctx.cancelled:
                    self.results[job.name] = {"status": "cancelled", "elapsed": 0}
                    self.dispatched.add(job.name)
                    self.done.add(job.name)
                    continue

                if getattr(job, "non_blocking", False):
                    self.results[job.name] = {
                        "status": "running_in_background", "elapsed": 0,
                    }
                    self.dispatched.add(job.name)
                    # Open a run record so the Jobs UI can list this
                    # async job before it completes.
                    try:
                        run_rec = _rh_start(
                            job.name,
                            parent_run_id=batch_id_for_children,
                            batch_id=batch_id_for_children,
                            triggered_by=self.source or "unknown",
                            actor=self.actor,
                        )
                        async_run_id = run_rec.run_id
                    except Exception as exc:  # noqa: BLE001
                        log_swallowed(exc)
                        async_run_id = None

                    def _run_async(j=job, _run_id=async_run_id):
                        _t = time.time()
                        try:
                            r = j.run(self.ctx) or {}
                        except Exception as exc:
                            r = {"status": "error", "error": str(exc)[:200]}
                        r["elapsed"] = round(time.time() - _t, 1)
                        self.results[j.name] = r
                        with self._cv:
                            self.done.add(j.name)
                            self._cv.notify_all()
                        runtime_platform.log(
                            f"[JOB] {j.name}: "
                            f"{r.get('status','?')} ({r['elapsed']}s) "
                            f"— non-blocking finished"
                        )
                        if _run_id is not None:
                            try:
                                _async_stdout = r.get("stdout_tail")
                                _rh_complete(
                                    _run_id,
                                    status=_terminal_status(r),
                                    error=r.get("error"),
                                    stdout_tail=(
                                        _async_stdout
                                        if isinstance(_async_stdout, str)
                                        else None
                                    ),
                                    log_anchor_source="controller",
                                    log_anchor_since_iso=_iso_at(_t),
                                    log_anchor_action=j.name,
                                )
                            except Exception as exc:  # noqa: BLE001
                                log_swallowed(exc)
                    threading.Thread(
                        target=_run_async, daemon=True,
                        name=f"job-async-{job.name}",
                    ).start()
                    runtime_platform.log(
                        f"[JOB] {job.name}: started (non-blocking) "
                        f"— {len(self.done)}/{len(all_jobs)} done, "
                        f"{sum(1 for j in all_jobs if j.name not in self.done)} remaining"
                    )
                    continue

                _t_job = time.time()
                # Open a run record before the synchronous job
                # executes; close with the terminal status afterward.
                _sync_run_id: str | None = None
                try:
                    _sync_rec = _rh_start(
                        job.name,
                        parent_run_id=batch_id_for_children,
                        batch_id=batch_id_for_children,
                        triggered_by=self.source or "unknown",
                        actor=self.actor,
                    )
                    _sync_run_id = _sync_rec.run_id
                except Exception as exc:  # noqa: BLE001
                    log_swallowed(exc)
                result = job.run(self.ctx)
                _job_elapsed = round(time.time() - _t_job, 1)
                self.dispatched.add(job.name)
                with self._cv:
                    self.done.add(job.name)
                    self._cv.notify_all()
                self.results[job.name] = result
                _status = (result or {}).get("status", "ok")
                if _sync_run_id is not None:
                    try:
                        _stdout_tail_value = (result or {}).get("stdout_tail")
                        _rh_complete(
                            _sync_run_id,
                            status=_terminal_status(result),
                            error=(result or {}).get("error"),
                            stdout_tail=(
                                _stdout_tail_value
                                if isinstance(_stdout_tail_value, str)
                                else None
                            ),
                            log_anchor_source="controller",
                            log_anchor_since_iso=_iso_at(_t_job),
                            log_anchor_action=job.name,
                        )
                    except Exception as exc:  # noqa: BLE001
                        log_swallowed(exc)
                _remaining = sum(
                    1 for j in all_jobs if j.name not in self.done
                )
                runtime_platform.log(
                    f"[JOB] {job.name}: {_status} ({_job_elapsed}s) "
                    f"— {len(self.done)}/{len(all_jobs)} done, "
                    f"{_remaining} remaining"
                )

        elapsed = round(time.time() - t0, 1)
        ok = sum(1 for r in self.results.values() if r.get("status") == "ok")
        skipped = sum(1 for r in self.results.values() if r.get("status") in ("skipped", "prereq_not_met"))
        errors = sum(1 for r in self.results.values() if r.get("status") == "error")
        runtime_platform.log(
            f"[INFO] JobRunner: complete — {ok} ok, {skipped} skipped, {errors} errors ({elapsed}s)"
        )
        result = {
            "status": "ok" if errors == 0 else "error",
            "elapsed": elapsed,
            "ok": ok,
            "skipped": skipped,
            "errors": errors,
            "jobs": self.results,
        }
        _record_history(result, source=self.source, actor=self.actor)
        # Close the parent batch run with a terminal status so the
        # Jobs UI can surface a single "last batch" entity.
        if batch_run is not None:
            try:
                _rh_complete(
                    batch_run.run_id,
                    status=_RS.OK if errors == 0 else _RS.ERROR,
                    log_anchor_source="controller",
                    log_anchor_since_iso=_iso_at(t0),
                )
            except Exception as exc:  # noqa: BLE001
                log_swallowed(exc)
        # ADR-0009 Phase 6.3 — emit ``job.completed`` / ``job.failed``
        # at batch end so the TriggerEngine can wire downstream Jobs
        # declaratively. The singleton ``fire`` is a no-op when no
        # dispatcher has been installed (early boot, unit tests that
        # don't care about triggers).
        try:
            from media_stack.application.jobs.trigger_dispatcher import (
                TriggerDispatcherSingleton,
            )
            TriggerDispatcherSingleton.fire(
                "job.completed" if errors == 0 else "job.failed",
                job=getattr(self.root, "name", ""),
                ctx=self.ctx,
            )
        except Exception as exc:  # noqa: BLE001
            log_swallowed(exc)
        # ADR-0009 Phase 6.4 (redo) — apply contract-declared
        # end-of-batch side-effects (``marks_setup_complete``
        # + ``retry_on_failure``) declaratively. The lifecycle handler
        # is a no-op when the root Job's contract entry has no such
        # fields — the common case for plugin and per-app Jobs.
        try:
            if errors == 0:
                self._apply_contract_lifecycle_metadata_on_success()
            else:
                self._apply_contract_lifecycle_metadata_on_failure()
        except Exception as exc:  # noqa: BLE001
            log_swallowed(exc)
        return result

    def _apply_contract_lifecycle_metadata_on_success(self) -> None:
        """Apply ``marks_setup_complete`` (if declared on the root
        Job's contract entry). Looks up the entry by name; no-op if
        the Job has no contract metadata. Split from the failure
        path to avoid a boolean flag argument."""
        job_def = self._lookup_root_contract_def()
        if job_def is None:
            return
        _JobLifecycleMetadataHandler.default().apply_on_completion(job_def)

    def _apply_contract_lifecycle_metadata_on_failure(self) -> None:
        """Apply ``retry_on_failure`` (if declared on the root
        Job's contract entry)."""
        job_def = self._lookup_root_contract_def()
        if job_def is None:
            return
        _JobLifecycleMetadataHandler.default().apply_on_failure(job_def)

    def _lookup_root_contract_def(self) -> dict[str, Any] | None:
        root_name = getattr(self.root, "name", "")
        return next(
            (
                j for j in discover_jobs_from_contracts()
                if j.get("name") == root_name
            ),
            None,
        )

    def _flatten(self, job: Job) -> list[Job]:
        """Flatten the job tree to a priority-ordered list.

        Parent handler runs as its own job. Sub-jobs are separate entries.
        This means prereqs are checked per-job, not per-subtree.
        """
        jobs: list[Job] = []
        # The parent job itself (handler only, sub-jobs handled separately)
        if job.handler is not _noop:
            jobs.append(job)
        for sub in job.sub_jobs:
            jobs.extend(self._flatten(sub))
        return jobs

# Module-level cancel flag — set by SIGTERM handler in subprocess
# (legacy) or by the in-process action loop (ADR-0005 Phase 5c.4).
# JobContext checks this so cancellation propagates through the job tree.
_cancel_requested = False


def request_cancel() -> None:
    """Signal cancellation from outside (e.g., SIGTERM handler, or
    the in-process action-watchdog when an action exceeds its
    timeout / the operator hits POST /cancel)."""
    global _cancel_requested
    _cancel_requested = True


def clear_cancel() -> None:
    """Reset the module-global cancel flag.

    The legacy subprocess shape didn't need this — the SIGTERM-set
    flag died with the subprocess at action end. The in-process
    action loop (ADR-0005 Phase 5c.4) reuses the same module
    namespace across actions, so each new dispatch must clear the
    flag or the second action would inherit the first's cancel.
    """
    global _cancel_requested
    _cancel_requested = False


def _is_cancel_requested() -> bool:
    return _cancel_requested


class JobContext:
    """Shared context for all bootstrap jobs."""

    def __init__(self):
        self.config_root = os.environ.get("CONFIG_ROOT", "/srv-config")
        self.wait_timeout = int(os.environ.get("BOOTSTRAP_WAIT_TIMEOUT", "180"))
        self.admin_username = os.environ.get("STACK_ADMIN_USERNAME", "admin")
        self.admin_password = os.environ.get("STACK_ADMIN_PASSWORD", "")
        self._cfg_cache: dict[str, Any] | None = None
        self._profile_cache: dict[str, Any] | None = None
        self._cancelled = False
        # ADR-0011 Phase 1 — domain is a leaf, ``Job.run`` reads
        # the logger from ``ctx.logger`` instead of importing
        # ``services.runtime_platform`` itself. Application layer
        # binds the real callable here at construction time.
        self.logger: Callable[[str], None] = runtime_platform.log

    @property
    def cancelled(self) -> bool:
        return self._cancelled or _is_cancel_requested()

    def cancel(self) -> None:
        """Mark this context as cancelled."""
        self._cancelled = True

    def check_cancelled(self) -> None:
        """Raise CancelledError if cancel has been requested."""
        if self.cancelled:
            raise CancelledError("cancelled by user")

    @property
    def cfg(self) -> dict[str, Any]:
        """Build config from service contract YAMLs + profile.

        Reads defaults directly from per-service YAML contracts
        (contracts/services/*.yaml), producing flat keys that handlers
        expect (e.g., jellyfin_libraries, jellyfin_livetv).
        """
        if self._cfg_cache is None:
            self._cfg_cache = _load_cfg_from_contracts(self.profile)
        return self._cfg_cache

    @property
    def profile(self) -> dict[str, Any]:
        """Load the profile YAML."""
        if self._profile_cache is None:
            profile_file = os.environ.get("BOOTSTRAP_PROFILE_FILE", "")
            if profile_file and Path(profile_file).is_file():
                self._profile_cache = yaml.safe_load(Path(profile_file).read_text(encoding="utf-8")) or {}
            else:
                self._profile_cache = {}
        return self._profile_cache

    def media_server_id(self) -> str:
        bindings = self.profile.get("technology_bindings", self.cfg.get("technology_bindings", {}))
        return str(bindings.get("media_server", "")).strip()

    def media_server_api_key(self) -> str:
        from media_stack.core.service_registry.registry import SERVICE_MAP
        ms_id = self.media_server_id()
        svc = SERVICE_MAP.get(ms_id)
        if svc and svc.api_key_env:
            return os.environ.get(svc.api_key_env, "")
        return ""

    def media_server_url(self) -> str:
        from media_stack.core.service_registry.registry import SERVICE_MAP
        ms_id = self.media_server_id()
        svc = SERVICE_MAP.get(ms_id)
        if svc:
            return f"http://{svc.host}:{svc.port}"
        return ""

    def api_key(self, service_id: str) -> str:
        """Resolve an API key for a service. Tries env var, then config file."""
        from media_stack.core.service_registry.registry import SERVICE_MAP, read_api_key_from_file
        svc = SERVICE_MAP.get(service_id)
        if not svc:
            return ""
        key = os.environ.get(svc.api_key_env or "", "").strip()
        if not key:
            key = read_api_key_from_file(service_id, self.config_root)
        return key

    def service_url(self, service_id: str) -> str:
        """Return the internal URL for a service from the registry."""
        from media_stack.core.service_registry.registry import SERVICE_MAP
        svc = SERVICE_MAP.get(service_id)
        if svc and svc.port > 0:
            return f"http://{svc.host}:{svc.port}"
        return ""


# ---------------------------------------------------------------------------
# Job implementations — each directly calls the app-layer function
# ---------------------------------------------------------------------------

def _ensure_media_server_api_key(ctx: JobContext) -> None:
    """Discover and set the media server API key if not already in env.

    Tries multiple sources in order:
    1. Environment variable (already set by preflight)
    2. Config file / DB read (registry reader)
    3. HTTP API key endpoint on the running service
    """
    ms_id = ctx.media_server_id()
    if not ms_id:
        return
    key = ctx.media_server_api_key()
    if key:
        return
    from media_stack.core.service_registry.registry import SERVICE_MAP, read_api_key_from_file, read_api_key_via_http
    svc = SERVICE_MAP.get(ms_id)
    if not svc or not svc.api_key_env:
        return
    # Try file/DB discovery
    discovered = read_api_key_from_file(ms_id, ctx.config_root)
    # Try HTTP discovery if file didn't work
    if not discovered:
        try:
            discovered = read_api_key_via_http(ms_id)
        except Exception as exc:
            runtime_platform.log(f"[DEBUG] {ms_id}: HTTP API key discovery failed: {exc}")
    if discovered:
        os.environ[svc.api_key_env] = discovered
        runtime_platform.log(f"[OK] {ms_id}: API key auto-discovered for job")


def _run_media_server_handler(ctx: JobContext, handler_suffix: str, label: str) -> dict[str, Any]:
    """Generic runner for media server handlers. Ensures API key is set first."""
    ms_id = ctx.media_server_id()
    if not ms_id:
        return {"skipped": "no media server configured"}
    _ensure_media_server_api_key(ctx)
    if not ctx.media_server_api_key():
        return {"skipped": f"no API key for {ms_id} — run bootstrap first"}
    try:
        mod = importlib.import_module(f"media_stack.services.apps.{ms_id}.runtime_ops")
        fn = getattr(mod, f"ensure_{ms_id}_{handler_suffix}", None)
        if fn:
            fn(ctx.cfg, ctx.config_root, ctx.wait_timeout)
            return {"service": ms_id}
        return {"skipped": f"no {label} handler for {ms_id}"}
    except Exception as exc:
        raise RuntimeError(f"{label} configuration failed: {exc}") from exc


    # All handler functions now live in their respective app modules.
    # Contracts point directly to them (e.g., configure_categories_job.py).


# ---------------------------------------------------------------------------
# Build the job tree
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Prerequisite registrations — media server conditions
# ---------------------------------------------------------------------------

def _prereq_media_server_id(ctx: "JobContext") -> bool:
    return bool(ctx.media_server_id())

def _prereq_media_server_api_key(ctx: "JobContext") -> bool:
    _ensure_media_server_api_key(ctx)
    return bool(ctx.media_server_api_key())

def _prereq_media_server_reachable(ctx: "JobContext") -> bool:
    url = ctx.media_server_url()
    if not url:
        return False
    try:
        import urllib.request
        with urllib.request.urlopen(f"{url}/System/Info/Public", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False

def _prereq_arr_apps_reachable(ctx: "JobContext") -> bool:
    """True when every *arr app whose container is in the registry
    responds to ``/api/v{N}/system/status`` within a short timeout.
    Used by ``validate-credentials`` so it doesn't fire while
    services are still warming up — the previous behaviour produced
    a cosmetic ``5/7 credential checks did not pass`` warning on
    every fresh install.

    URLs come from the service registry (``ctx.service_url()``), NOT
    from ``ctx.cfg`` — the flat-cfg layout doesn't carry per-service
    URLs because they're derived from the container hostname/port at
    runtime. Same for API keys: ``ctx.api_key()`` reads the env var
    set by discover-api-keys, falling back to the on-disk config.

    Returns True when no *arr apps are present in the registry (no
    preconditions to satisfy) so single-service profiles aren't
    blocked."""
    import urllib.request
    from media_stack.core.service_registry.registry import SERVICE_MAP
    arr_specs = (
        ("sonarr", "v3"), ("radarr", "v3"), ("lidarr", "v1"),
        ("readarr", "v1"), ("prowlarr", "v1"),
    )
    checked = 0
    for name, ver in arr_specs:
        if name not in SERVICE_MAP:
            continue
        url = ctx.service_url(name)
        api_key = ctx.api_key(name)
        if not url or not api_key:
            # No key yet → discover-api-keys hasn't read it from
            # the service config. Treat as "not ready" so the
            # gate keeps waiting rather than letting validate-
            # credentials fire too early.
            return False
        checked += 1
        req = urllib.request.Request(
            f"{url.rstrip('/')}/api/{ver}/system/status",
            headers={"X-Api-Key": api_key},
        )
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                if r.status != 200:
                    return False
        except Exception:
            return False
    return True

register_prereq("media_server_id", _prereq_media_server_id)
register_prereq("media_server_api_key", _prereq_media_server_api_key)
register_prereq("media_server_reachable", _prereq_media_server_reachable)
register_prereq("arr_apps_reachable", _prereq_arr_apps_reachable)


# ---------------------------------------------------------------------------
# Job tree definitions — these are specific to *this* workflow.
# Delete them and define a completely different workflow. The framework
# (Job, JobRunner, PREREQS) doesn't change.
# ---------------------------------------------------------------------------

def _resolve_handler(handler_path: str) -> Callable:
    """Import a handler from a dotted module:function path."""
    if ":" in handler_path:
        mod_path, func_name = handler_path.rsplit(":", 1)
    elif "." in handler_path:
        mod_path, func_name = handler_path.rsplit(".", 1)
    else:
        raise ValueError(f"Invalid handler path: {handler_path}")
    mod = importlib.import_module(mod_path)
    fn = getattr(mod, func_name, None)
    if fn is None:
        raise AttributeError(f"{handler_path}: function not found")
    return fn


def _make_handler_wrapper(handler_fn: Callable, service_id: str) -> Callable:
    """Wrap a raw ensure_* handler (cfg, config_root, timeout) as a Job handler (ctx)."""
    def wrapper(ctx: "JobContext") -> dict[str, Any]:
        handler_fn(ctx.cfg, ctx.config_root, ctx.wait_timeout)
        return {"service": service_id}
    return wrapper


_DISCOVERED_JOBS_CACHE: list[dict[str, Any]] | None = None

def discover_jobs_from_contracts() -> list[dict[str, Any]]:
    """Scan service contracts for job definitions. Cached after first call.

    Returns flat list of job defs:
        [{"name": "configure-libraries", "handler": "...", "phase": "media_server",
          "priority": 10, "requires": [...], "service": "jellyfin"}, ...]
    """
    global _DISCOVERED_JOBS_CACHE
    if _DISCOVERED_JOBS_CACHE is not None:
        return _DISCOVERED_JOBS_CACHE
    svc_dir = _find_contracts_dir()
    if not svc_dir:
        return []

    jobs: list[dict[str, Any]] = []
    for svc_yaml in sorted(svc_dir.glob("*.yaml")):
        if svc_yaml.name.startswith("_"):
            continue
        try:
            svc_data = yaml.safe_load(svc_yaml.read_text(encoding="utf-8")) or {}
            svc_id = svc_data.get("service", {}).get("id", "")
            plugin = svc_data.get("plugin", {})
            job_defs = plugin.get("jobs", {})
            if not isinstance(job_defs, dict) or not svc_id:
                continue
            for job_name, job_def in job_defs.items():
                if not isinstance(job_def, dict):
                    continue
                jobs.append({
                    "name": job_name,
                    "handler": job_def.get("handler", ""),
                    "phase": job_def.get("phase", "default"),
                    "priority": int(job_def.get("priority", 50)),
                    "requires": list(job_def.get("requires", [])),
                    # ``max_attempts`` is optional. ``None`` means
                    # "use the framework default" (currently 3).
                    "max_attempts": (
                        int(job_def["max_attempts"])
                        if "max_attempts" in job_def
                        else None
                    ),
                    # ``non_blocking: true`` makes the runner spawn
                    # the job in a daemon thread and immediately move
                    # on to the next job without waiting. Use for
                    # slow probes (indexer discovery, EPG channel
                    # scan) so the dashboard becomes usable before
                    # they complete. Default false = blocking.
                    "non_blocking": bool(job_def.get("non_blocking", False)),
                    # ``after: [job-name, ...]`` — wait for those jobs
                    # to FULLY COMPLETE before this one starts. Distinct
                    # from ``requires:`` (named conditions). Use this
                    # when a downstream job needs the SIDE EFFECTS of an
                    # upstream non_blocking job (e.g. tag-indexers needs
                    # the indexers discover-indexers added).
                    "after": list(job_def.get("after", [])),
                    # ADR-0009 Phase 6.1 — declarative triggers. Each
                    # entry is a dict ``{event: <kind>, ...}``; the
                    # ``TriggerEngine`` validates kinds (``manual``,
                    # ``schedule``, ``job.completed``, ``job.failed``,
                    # ``promise.satisfied``, ``promise.violated``,
                    # ``controller.started``) and ``when:`` predicate
                    # names against the closed registry at boot.
                    # Absence = job is manual-only (today's behavior).
                    # The key is ``event:`` not ``on:`` because PyYAML
                    # parses bare ``on:`` as the boolean ``True``
                    # (YAML 1.1's deprecated ``on/off`` alias).
                    "triggers": list(job_def.get("triggers", [])),
                    # ADR-0010 Phase 7.1 — promises this Job's
                    # successful completion satisfies. Loader emits
                    # ``promise.satisfied`` to the TriggerEngine for
                    # each entry on Job.complete; downstream Jobs
                    # subscribed via ``triggers: [event:
                    # promise.satisfied, scope: …]`` fire
                    # automatically. Replaces the legacy
                    # lifecycle-wirer promise→ensurer mapping.
                    "satisfies": list(job_def.get("satisfies", [])),
                    # ADR-0009 Phase 6.4 (redo) — declarative
                    # end-of-batch side-effects on the Job that owns
                    # them. Replace the per-Job handler files
                    # (``mark_initial_bootstrap_done.py`` etc.) with
                    # contract metadata; ``JobLifecycleMetadataHandler``
                    # reads them at end-of-batch and applies the
                    # requested side-effect.
                    #
                    # ``marks_setup_complete`` (bool):
                    # framework calls
                    # ``ControllerState.mark_initial_bootstrap_done()``
                    # on Job success. Multiple Jobs may set this;
                    # the call is idempotent at the state level.
                    #
                    # ``retry_on_failure``: ``{target, delay_seconds,
                    # when}`` dict. Schedules a daemon timer for the
                    # delay, then dispatches ``target`` via the
                    # trigger dispatcher when the predicate ``when``
                    # passes (omitted/unknown predicate = always-on).
                    "marks_setup_complete": bool(
                        job_def.get("marks_setup_complete", False),
                    ),
                    "retry_on_failure": (
                        dict(job_def["retry_on_failure"])
                        if isinstance(
                            job_def.get("retry_on_failure"), dict,
                        )
                        else None
                    ),
                    # When true, this entry exists for metadata only
                    # (e.g., the synthesized ``bootstrap`` root). The
                    # tree builder skips it; the trigger index +
                    # lifecycle metadata reader still see it.
                    "tree_skip": bool(job_def.get("tree_skip", False)),
                    # ``phase_order`` is read by ``build_job_framework``
                    # from the synthesized-root entry (``tree_skip:
                    # true``) to decide how per-phase groups are
                    # sequenced. Phases not in this list get appended
                    # alphabetically after, so plugin-defined phases
                    # work without editing the root entry.
                    "phase_order": list(job_def.get("phase_order", []) or []),
                    # Human-readable label shown in the dashboard
                    # toast / job tree / activity feed. Falls back
                    # to a slug → Title Case translation if
                    # missing. The label lives WITH the job
                    # definition (not in dashboard.html) so adding
                    # a new contract job is one-place.
                    "label": str(job_def.get("label") or "").strip(),
                    "service": svc_id,
                })
        except Exception as exc:
            runtime_platform.log(f"[DEBUG] Failed to discover jobs from {svc_yaml.name}: {exc}")
            continue

    _DISCOVERED_JOBS_CACHE = sorted(jobs, key=lambda j: (j["phase"], j["priority"]))
    return _DISCOVERED_JOBS_CACHE


_DISCOVERED_ALIASES_CACHE: dict[str, str] | None = None


def discover_job_aliases() -> dict[str, str]:
    """Walk every service contract for a ``plugin.job_aliases`` map
    and return the merged ``alias -> canonical`` dict.

    Aliases are job metadata, not dispatch code. Putting them in
    the YAML keeps the dispatch a single ``run_job(name)`` call:
    when the dashboard hits ``/actions/reconcile``, ``run_job``
    resolves the alias to ``bootstrap`` and walks the same tree
    everything else does. New aliases ship by editing one YAML
    file — the kind of change a third-party developer can make
    without touching any Python.

    First-write-wins on collisions, with a debug log. The cache
    matches the discover_jobs_from_contracts cache lifecycle so
    tests can clear both with ``_DISCOVERED_*_CACHE = None``."""
    global _DISCOVERED_ALIASES_CACHE
    if _DISCOVERED_ALIASES_CACHE is not None:
        return _DISCOVERED_ALIASES_CACHE
    svc_dir = _find_contracts_dir()
    if not svc_dir:
        _DISCOVERED_ALIASES_CACHE = {}
        return _DISCOVERED_ALIASES_CACHE

    aliases: dict[str, str] = {}
    for svc_yaml in sorted(svc_dir.glob("*.yaml")):
        if svc_yaml.name.startswith("_"):
            continue
        try:
            svc_data = yaml.safe_load(svc_yaml.read_text(encoding="utf-8")) or {}
            plugin = svc_data.get("plugin", {})
            raw = plugin.get("job_aliases", {})
            if not isinstance(raw, dict):
                continue
            for alias, canonical in raw.items():
                if not isinstance(alias, str) or not isinstance(canonical, str):
                    continue
                if alias in aliases and aliases[alias] != canonical:
                    runtime_platform.log(
                        f"[DEBUG] job alias collision for {alias!r}: "
                        f"{aliases[alias]!r} (kept) vs {canonical!r} "
                        f"(from {svc_yaml.name})"
                    )
                    continue
                aliases[alias] = canonical
        except Exception as exc:
            runtime_platform.log(
                f"[DEBUG] Failed to discover aliases from "
                f"{svc_yaml.name}: {exc}"
            )
            continue

    _DISCOVERED_ALIASES_CACHE = aliases
    return aliases


def resolve_alias(name: str) -> str:
    """Return the canonical job name for ``name``. Falls through
    when ``name`` isn't an alias — so callers can always invoke
    this safely. Resolves transitively in case a future alias
    points at another alias (capped at 8 hops to avoid loops)."""
    aliases = discover_job_aliases()
    seen: set[str] = set()
    current = name
    for _ in range(8):
        if current in seen:
            return current  # cycle guard
        seen.add(current)
        nxt = aliases.get(current)
        if nxt is None or nxt == current:
            return current
        current = nxt
    return current


def build_job_framework() -> Job:
    """Build the bootstrap job tree by scanning service contracts.

    No hardcoded job list. Each service declares its own jobs in its
    YAML contract. The tree is grouped by phase.

    Phases (execution order):
      media_server → download_clients → post

    Add a service with jobs → they appear automatically.
    Remove a service → its jobs disappear.
    """
    discovered = discover_jobs_from_contracts()
    # ``phase_order`` and ``max_attempts`` for the synthesized root
    # come from the ``tree_skip:true`` ``bootstrap`` entry in
    # ``contracts/services/core.yaml`` — keeps phase sequencing and
    # cross-cutting retry budget out of framework code (ADR-0009
    # Phase 6.4 redo). Missing entry falls back to empty
    # phase_order + default max_attempts so test fixtures that
    # ship a stripped contracts dir keep working.
    _root_entry = next(
        (
            j for j in discovered
            if j.get("name") == "bootstrap" and j.get("tree_skip")
        ),
        None,
    )
    _root_max_attempts = (
        _root_entry.get("max_attempts") if _root_entry else None
    ) or 30
    phase_order: list[str] = list(
        _root_entry.get("phase_order", []) if _root_entry else []
    )
    root = Job("bootstrap", _noop, max_attempts=_root_max_attempts)

    # Group by phase, skipping metadata-only entries (the synthesized
    # ``bootstrap`` root carries declarative end-of-batch metadata
    # via a ``tree_skip: true`` contract entry that must NOT appear
    # again as a sub-job — see ADR-0009 Phase 6.4 redo notes).
    phases: dict[str, list[dict[str, Any]]] = {}
    for j in discovered:
        if j.get("tree_skip"):
            continue
        phases.setdefault(j["phase"], []).append(j)
    for phase_name in phase_order:
        phase_jobs = phases.pop(phase_name, [])
        if not phase_jobs:
            continue
        phase_label = f"configure-{phase_name.replace('_', '-')}"
        # Collect prereqs from children for the phase group
        phase_prereqs = set()
        for j in phase_jobs:
            phase_prereqs.update(j.get("requires", []))
        phase_job = Job(phase_label, _noop, requires=sorted(phase_prereqs))
        for j in phase_jobs:
            handler_path = j["handler"]
            try:
                raw_fn = _resolve_handler(handler_path)
                # If handler takes (ctx) → use directly; if (cfg, root, timeout) → wrap
                import inspect
                sig = inspect.signature(raw_fn)
                params = list(sig.parameters.keys())
                if len(params) == 1 and params[0] == "ctx":
                    handler = raw_fn
                elif len(params) >= 2:
                    handler = _make_handler_wrapper(raw_fn, j["service"])
                else:
                    handler = raw_fn
            except Exception:
                runtime_platform.log(f"[WARN] Cannot resolve handler for job {j['name']}: {handler_path}")
                continue
            phase_job.add_sub_job(Job(
                j["name"], handler,
                requires=j.get("requires", []),
                max_attempts=j.get("max_attempts"),
                non_blocking=j.get("non_blocking", False),
                after=j.get("after", []),
            ))
        root.add_sub_job(phase_job)

    # Any remaining phases
    for phase_name, phase_jobs in sorted(phases.items()):
        phase_job = Job(f"configure-{phase_name}", _noop)
        for j in phase_jobs:
            try:
                raw_fn = _resolve_handler(j["handler"])
                handler = _make_handler_wrapper(raw_fn, j["service"])
                phase_job.add_sub_job(Job(
                    j["name"], handler,
                    requires=j.get("requires", []),
                    max_attempts=j.get("max_attempts"),
                    non_blocking=j.get("non_blocking", False),
                ))
            except Exception as exc:
                runtime_platform.log(f"[WARN] Cannot resolve handler for remaining-phase job {j['name']}: {exc}")
                continue
        root.add_sub_job(phase_job)

    return root


# ---------------------------------------------------------------------------
# Flat job registry for individual action dispatch
# ---------------------------------------------------------------------------

def get_job_registry() -> dict[str, Callable[[JobContext], dict[str, Any]]]:
    """Return flat map of job-name → handler, discovered from contracts."""
    root = build_job_framework()
    registry: dict[str, Callable] = {}
    def _collect(job: Job) -> None:
        if job.handler is not _noop:
            registry[job.name] = job.handler
        for sub in job.sub_jobs:
            _collect(sub)
    _collect(root)
    return registry


_DEFAULT_JOB_MAX_ATTEMPTS = 3


def run_job(
    job_name: str,
    *,
    source: str | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    """Run a single job by name. Uses JobRunner for prereq waiting.

    Resolves contract-declared aliases first (so callers can pass
    user-facing names like ``reconcile`` and reach the canonical
    job ``bootstrap``), then reads ``max_attempts`` from the
    resolved Job instance — the contract is the source of truth.
    No more ``run_job(name, max_wait=180)`` callers; if a job
    needs special timing it declares it in YAML.

    ``source`` / ``actor`` are optional and propagate through to
    the history entry written by ``JobRunner.run`` so the dashboard
    can show ``cron`` / ``manual`` / ``auto-heal`` badges. Default
    values keep older callers (e.g. test fixtures) working without
    a code change.
    """
    job_name = resolve_alias(job_name)
    root = build_job_framework()
    job = _find_job_in_tree(root, job_name)
    if not job:
        registry = get_job_registry()
        handler = registry.get(job_name)
        if not handler:
            return {"error": f"Unknown job: {job_name}", "known": sorted(registry.keys())}
        job = Job(job_name, handler)
    ctx = JobContext()
    attempts = job.max_attempts if job.max_attempts is not None else _DEFAULT_JOB_MAX_ATTEMPTS
    return JobRunner(
        job, ctx, max_attempts=attempts, source=source, actor=actor,
    ).run()


def run_all_media_server_jobs(
    max_wait: int = 180,
    *,
    source: str | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    """Run all media server configuration jobs.

    Uses JobRunner which waits for prerequisites with active retry
    before executing the tree. ``source`` / ``actor`` propagate
    into the recorded history entry (see ``run_job``).
    """
    ctx = JobContext()
    root = build_job_framework()
    return JobRunner(
        root, ctx, max_wait=max_wait, source=source, actor=actor,
    ).run()


def _find_job_in_tree(root: Job, name: str) -> Job | None:
    """Find a job by name in a tree (DFS)."""
    if root.name == name:
        return root
    for sub in root.sub_jobs:
        found = _find_job_in_tree(sub, name)
        if found:
            return found
    return None
