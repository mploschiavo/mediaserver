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

import importlib
import os
import time
from pathlib import Path
from typing import Any, Callable

import yaml

import media_stack.services.runtime_platform as runtime_platform


# ---------------------------------------------------------------------------
# Config loading from service contracts
# ---------------------------------------------------------------------------

def _find_contracts_dir() -> Path | None:
    """Locate the contracts/services/ YAML directory."""
    candidates = [
        Path(os.environ.get("SERVICES_REGISTRY_DIR", "")) if os.environ.get("SERVICES_REGISTRY_DIR") else None,
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
            svc_data = yaml.safe_load(svc_yaml.read_text()) or {}
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
        except Exception:
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

_JOB_HISTORY_MAX = 20


def _history_file() -> Path:
    config_root = os.environ.get("CONFIG_ROOT", "/srv-config")
    return Path(config_root) / ".controller" / "job-history.json"


def get_job_history() -> list[dict[str, Any]]:
    """Return recent job execution history (newest first). Reads from disk."""
    path = _history_file()
    if not path.is_file():
        return []
    try:
        import json
        entries = json.loads(path.read_text())
        return list(reversed(entries)) if isinstance(entries, list) else []
    except Exception:
        return []


def _record_history(result: dict[str, Any]) -> None:
    """Record a job run result in history. Writes to disk (survives subprocess)."""
    import json
    entry = {
        "ts": time.time(),
        "elapsed": result.get("elapsed", 0),
        "ok": result.get("ok", 0),
        "skipped": result.get("skipped", 0),
        "errors": result.get("errors", 0),
        "jobs": {
            name: {
                "status": r.get("status", "?"),
                "elapsed": r.get("elapsed", 0),
            }
            for name, r in result.get("jobs", {}).items()
        },
    }
    path = _history_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = json.loads(path.read_text()) if path.is_file() else []
        if not isinstance(existing, list):
            existing = []
        existing.append(entry)
        if len(existing) > _JOB_HISTORY_MAX:
            existing = existing[-_JOB_HISTORY_MAX:]
        path.write_text(json.dumps(existing))
    except Exception:
        pass


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

# Named prerequisite registry — any module can register conditions.
# Each is a callable(JobContext) -> bool.
PREREQS: dict[str, Callable[["JobContext"], bool]] = {}


def register_prereq(name: str, check: Callable[["JobContext"], bool]) -> None:
    """Register a named prerequisite condition. Idempotent."""
    PREREQS[name] = check


class Job:
    """A unit of work with optional prerequisites and sub-jobs.

    This is the framework. It knows nothing about Jellyfin, bootstrap,
    or media servers. Any workflow can use it.

    - ``requires``: list of PREREQS names that must be True before running
    - ``sub_jobs``: child jobs that run after this job's handler succeeds
    - Sub-jobs inherit no prereqs from parents — each declares its own
    - N-level nesting: sub-jobs can have sub-jobs
    """

    def __init__(
        self,
        name: str,
        handler: Callable[["JobContext"], dict[str, Any] | None],
        requires: list[str] | None = None,
    ):
        self.name = name
        self.handler = handler
        self.requires = requires or []
        self.sub_jobs: list["Job"] = []

    def add_sub_job(self, job: "Job") -> "Job":
        """Add a child job. Returns self for chaining."""
        self.sub_jobs.append(job)
        return self

    def check_prereqs(self, ctx: "JobContext") -> str | None:
        """Check all prerequisites. Returns failure reason or None if all pass."""
        for req_name in self.requires:
            check_fn = PREREQS.get(req_name)
            if check_fn and not check_fn(ctx):
                return f"prerequisite '{req_name}' not met"
        return None

    def run(self, ctx: "JobContext") -> dict[str, Any]:
        """Run this job's handler, then sub-jobs. Checks prereqs first."""
        # Check cancel before starting
        if ctx.cancelled:
            return {"status": "cancelled", "elapsed": 0}

        runtime_platform.log(f"[JOB] {self.name}: starting")
        runtime_platform.log(f"[DEBUG] Job {self.name}: requires={self.requires}, "
                             f"sub_jobs=[{', '.join(s.name for s in self.sub_jobs)}], "
                             f"handler={self.handler.__module__}.{self.handler.__name__}")
        t0 = time.time()

        # Gate on prerequisites
        prereq_fail = self.check_prereqs(ctx)
        if prereq_fail:
            elapsed = round(time.time() - t0, 1)
            runtime_platform.log(
                f"[WAIT] {self.name}: {prereq_fail} ({elapsed}s)"
            )
            return {"status": "prereq_not_met", "reason": prereq_fail, "elapsed": elapsed}

        try:
            result = self.handler(ctx) or {}
            if "skipped" in result:
                reason = result["skipped"]
                elapsed = round(time.time() - t0, 1)
                runtime_platform.log(
                    f"[WARN] {self.name}: SKIPPED — {reason} ({elapsed}s)"
                )
                return {"status": "skipped", "elapsed": elapsed, **result}
            # Run sub-jobs (each checks its own prereqs)
            for sub in self.sub_jobs:
                if ctx.cancelled:
                    runtime_platform.log(f"[ACTION] {self.name}: cancelled before sub-job {sub.name}")
                    break
                try:
                    sub.run(ctx)
                except CancelledError:
                    break
                except Exception as exc:
                    runtime_platform.log(f"[WARN] {self.name}/{sub.name}: {exc}")
            if ctx.cancelled:
                elapsed = round(time.time() - t0, 1)
                return {"status": "cancelled", "elapsed": elapsed}
            elapsed = round(time.time() - t0, 1)
            runtime_platform.log(f"[OK] {self.name}: complete ({elapsed}s)")
            return {"status": "ok", "elapsed": elapsed, **result}
        except CancelledError:
            elapsed = round(time.time() - t0, 1)
            runtime_platform.log(f"[ACTION] {self.name}: cancelled ({elapsed}s)")
            return {"status": "cancelled", "elapsed": elapsed}
        except Exception as exc:
            elapsed = round(time.time() - t0, 1)
            runtime_platform.log(f"[ERR] {self.name}: {exc} ({elapsed}s)")
            import traceback as _tb
            runtime_platform.log(f"[DEBUG] Job {self.name} traceback:\n{_tb.format_exc()}")
            return {"status": "error", "error": str(exc)[:1000], "elapsed": elapsed}


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

    def __init__(self, root: Job, ctx: "JobContext", max_attempts: int = 3, **kwargs: Any):
        self.root = root
        self.ctx = ctx
        self.max_attempts = kwargs.get("max_wait", max_attempts) if "max_wait" in kwargs else max_attempts
        self.completed: set[str] = set()
        self.results: dict[str, dict[str, Any]] = {}

    def run(self) -> dict[str, Any]:
        """Execute all jobs in dependency order."""
        t0 = time.time()
        all_jobs = self._flatten(self.root)
        runtime_platform.log(f"[INFO] JobRunner: {len(all_jobs)} jobs to dispatch")

        attempt = 0
        while attempt <= self.max_attempts:
            # Find jobs that are ready (prereqs met, not yet run)
            ready = [j for j in all_jobs
                     if j.name not in self.completed and j.check_prereqs(self.ctx) is None]
            deferred = [j for j in all_jobs
                        if j.name not in self.completed and j.check_prereqs(self.ctx) is not None]

            if not ready and not deferred:
                break  # All done

            if not ready and deferred:
                # No jobs ready — try to satisfy prereqs
                attempt += 1
                if attempt > self.max_attempts:
                    for j in deferred:
                        reason = j.check_prereqs(self.ctx)
                        runtime_platform.log(f"[WARN] {j.name}: deferred — {reason}")
                        self.results[j.name] = {"status": "prereq_not_met", "reason": reason}
                    break
                runtime_platform.log(
                    f"[INFO] JobRunner: {len(deferred)} jobs waiting on prereqs, "
                    f"attempting to satisfy (attempt {attempt}/{self.max_attempts})"
                )
                self._try_satisfy_prereqs()
                continue

            # Reset attempt counter — we made progress
            attempt = 0

            # Run all ready jobs
            for job in ready:
                if self.ctx.cancelled:
                    self.results[job.name] = {"status": "cancelled", "elapsed": 0}
                    self.completed.add(job.name)
                    continue
                result = job.run(self.ctx)
                self.completed.add(job.name)
                self.results[job.name] = result

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
        _record_history(result)
        return result

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

    def _try_satisfy_prereqs(self) -> None:
        """Actively try to satisfy prereqs (e.g., run media server preflight)."""
        ms_id = self.ctx.media_server_id()
        if not ms_id:
            return
        try:
            preflight_mod = importlib.import_module(f"media_stack.services.apps.{ms_id}.http_preflight")
            run_preflight = preflight_mod.run_preflight
            result = run_preflight(
                config_root=self.ctx.config_root,
                admin_username=self.ctx.admin_username,
                admin_password=self.ctx.admin_password,
                log=runtime_platform.log,
            )
            from media_stack.api.services.registry import SERVICE_MAP
            svc = SERVICE_MAP.get(ms_id)
            if svc and result and result.get(svc.api_key_env):
                os.environ[svc.api_key_env] = result[svc.api_key_env]
                runtime_platform.log(f"[OK] {ms_id}: API key obtained via preflight")
        except Exception as exc:
            runtime_platform.log(f"[INFO] Preflight attempt: {exc}")


class CancelledError(RuntimeError):
    """Raised when a job is cancelled."""


# Module-level cancel flag — set by SIGTERM handler in subprocess.
# JobContext checks this so cancellation propagates through the job tree.
_cancel_requested = False


def request_cancel() -> None:
    """Signal cancellation from outside (e.g., SIGTERM handler)."""
    global _cancel_requested
    _cancel_requested = True


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
                self._profile_cache = yaml.safe_load(Path(profile_file).read_text()) or {}
            else:
                self._profile_cache = {}
        return self._profile_cache

    def media_server_id(self) -> str:
        bindings = self.profile.get("technology_bindings", self.cfg.get("technology_bindings", {}))
        return str(bindings.get("media_server", "")).strip()

    def media_server_api_key(self) -> str:
        from media_stack.api.services.registry import SERVICE_MAP
        ms_id = self.media_server_id()
        svc = SERVICE_MAP.get(ms_id)
        if svc and svc.api_key_env:
            return os.environ.get(svc.api_key_env, "")
        return ""

    def media_server_url(self) -> str:
        from media_stack.api.services.registry import SERVICE_MAP
        ms_id = self.media_server_id()
        svc = SERVICE_MAP.get(ms_id)
        if svc:
            return f"http://{svc.host}:{svc.port}"
        return ""

    def api_key(self, service_id: str) -> str:
        """Resolve an API key for a service. Tries env var, then config file."""
        from media_stack.api.services.registry import SERVICE_MAP, read_api_key_from_file
        svc = SERVICE_MAP.get(service_id)
        if not svc:
            return ""
        key = os.environ.get(svc.api_key_env or "", "").strip()
        if not key:
            key = read_api_key_from_file(service_id, self.config_root)
        return key

    def service_url(self, service_id: str) -> str:
        """Return the internal URL for a service from the registry."""
        from media_stack.api.services.registry import SERVICE_MAP
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
    from media_stack.api.services.registry import SERVICE_MAP, read_api_key_from_file, read_api_key_via_http
    svc = SERVICE_MAP.get(ms_id)
    if not svc or not svc.api_key_env:
        return
    # Try file/DB discovery
    discovered = read_api_key_from_file(ms_id, ctx.config_root)
    # Try HTTP discovery if file didn't work
    if not discovered:
        try:
            discovered = read_api_key_via_http(ms_id)
        except Exception:
            pass
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


def _noop(ctx: JobContext) -> dict[str, Any]:
    """Placeholder for composite jobs."""
    return {}


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

register_prereq("media_server_id", _prereq_media_server_id)
register_prereq("media_server_api_key", _prereq_media_server_api_key)
register_prereq("media_server_reachable", _prereq_media_server_reachable)


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
            svc_data = yaml.safe_load(svc_yaml.read_text()) or {}
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
                    "service": svc_id,
                })
        except Exception:
            continue

    _DISCOVERED_JOBS_CACHE = sorted(jobs, key=lambda j: (j["phase"], j["priority"]))
    return _DISCOVERED_JOBS_CACHE


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
    root = Job("bootstrap", _noop)

    # Group by phase
    phases: dict[str, list[dict[str, Any]]] = {}
    for j in discovered:
        phases.setdefault(j["phase"], []).append(j)

    # Phase ordering
    phase_order = ["media_server", "download_clients", "default", "post"]
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
            phase_job.add_sub_job(Job(j["name"], handler, requires=j.get("requires", [])))
        root.add_sub_job(phase_job)

    # Any remaining phases
    for phase_name, phase_jobs in sorted(phases.items()):
        phase_job = Job(f"configure-{phase_name}", _noop)
        for j in phase_jobs:
            try:
                raw_fn = _resolve_handler(j["handler"])
                handler = _make_handler_wrapper(raw_fn, j["service"])
                phase_job.add_sub_job(Job(j["name"], handler, requires=j.get("requires", [])))
            except Exception:
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


def run_job(job_name: str, max_wait: int = 30) -> dict[str, Any]:
    """Run a single job by name. Uses JobRunner for prereq waiting."""
    # Look up the job from the bootstrap tree to get its prereqs
    root = build_job_framework()
    job = _find_job_in_tree(root, job_name)
    if not job:
        # Fall back to registry (no prereqs)
        registry = get_job_registry()
        handler = registry.get(job_name)
        if not handler:
            return {"error": f"Unknown job: {job_name}", "known": sorted(registry.keys())}
        job = Job(job_name, handler)
    ctx = JobContext()
    return JobRunner(job, ctx, max_wait=max_wait).run()


def run_all_media_server_jobs(max_wait: int = 180) -> dict[str, Any]:
    """Run all media server configuration jobs.

    Uses JobRunner which waits for prerequisites with active retry
    before executing the tree.
    """
    ctx = JobContext()
    root = build_job_framework()
    return JobRunner(root, ctx, max_wait=max_wait).run()


def _find_job_in_tree(root: Job, name: str) -> Job | None:
    """Find a job by name in a tree (DFS)."""
    if root.name == name:
        return root
    for sub in root.sub_jobs:
        found = _find_job_in_tree(sub, name)
        if found:
            return found
    return None
