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
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

import yaml

import media_stack.services.runtime_platform as runtime_platform


# ---------------------------------------------------------------------------
# Job registry
# ---------------------------------------------------------------------------

class Job:
    """A single bootstrap job with optional sub-jobs."""

    def __init__(self, name: str, handler: Callable, depends_on: list[str] | None = None):
        self.name = name
        self.handler = handler
        self.depends_on = depends_on or []
        self.sub_jobs: list[Job] = []

    def add_sub_job(self, job: "Job") -> None:
        self.sub_jobs.append(job)

    def run(self, ctx: "JobContext") -> dict[str, Any]:
        """Run this job and all sub-jobs."""
        runtime_platform.log(f"[JOB] {self.name}: starting")
        t0 = time.time()
        try:
            result = self.handler(ctx)
            # Run sub-jobs
            for sub in self.sub_jobs:
                try:
                    sub.run(ctx)
                except Exception as exc:
                    runtime_platform.log(f"[WARN] {self.name}/{sub.name}: {exc}")
            elapsed = round(time.time() - t0, 1)
            runtime_platform.log(f"[OK] {self.name}: complete ({elapsed}s)")
            return {"status": "ok", "elapsed": elapsed, **(result or {})}
        except Exception as exc:
            elapsed = round(time.time() - t0, 1)
            runtime_platform.log(f"[ERR] {self.name}: {exc} ({elapsed}s)")
            return {"status": "error", "error": str(exc)[:200], "elapsed": elapsed}


class JobContext:
    """Shared context for all bootstrap jobs."""

    def __init__(self):
        self.config_root = os.environ.get("CONFIG_ROOT", "/srv-config")
        self.wait_timeout = int(os.environ.get("BOOTSTRAP_WAIT_TIMEOUT", "180"))
        self.admin_username = os.environ.get("STACK_ADMIN_USERNAME", "admin")
        self.admin_password = os.environ.get("STACK_ADMIN_PASSWORD", "")
        self._cfg_cache: dict[str, Any] | None = None
        self._profile_cache: dict[str, Any] | None = None

    @property
    def cfg(self) -> dict[str, Any]:
        """Load the bootstrap config (generated or from file)."""
        if self._cfg_cache is None:
            from media_stack.cli.commands.controller_handlers import _resolve_config_path
            config_path = _resolve_config_path(os.environ.get("BOOTSTRAP_CONFIG_FILE"))
            if config_path:
                self._cfg_cache = json.loads(Path(config_path).read_text())
            else:
                self._cfg_cache = {}
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


# ---------------------------------------------------------------------------
# Job implementations — each directly calls the app-layer function
# ---------------------------------------------------------------------------

def _ensure_media_server_api_key(ctx: JobContext) -> None:
    """Discover and set the media server API key if not already in env."""
    ms_id = ctx.media_server_id()
    if not ms_id:
        return
    key = ctx.media_server_api_key()
    if key:
        return
    # Auto-discover from config file
    from media_stack.api.services.registry import SERVICE_MAP, read_api_key_from_file
    svc = SERVICE_MAP.get(ms_id)
    if svc and svc.api_key_env:
        discovered = read_api_key_from_file(ms_id, ctx.config_root)
        if discovered:
            os.environ[svc.api_key_env] = discovered
            runtime_platform.log(f"[OK] {ms_id}: API key auto-discovered for job")


def _configure_libraries(ctx: JobContext) -> dict[str, Any]:
    """Create media server libraries (Movies, TV Shows, Music, Books)."""
    ms_id = ctx.media_server_id()
    if not ms_id:
        return {"skipped": "no media server configured"}
    _ensure_media_server_api_key(ctx)
    try:
        mod = importlib.import_module(f"media_stack.services.apps.{ms_id}.runtime_ops")
        fn = getattr(mod, f"ensure_{ms_id}_libraries", None)
        if fn:
            fn(ctx.cfg, ctx.config_root, ctx.wait_timeout)
            return {"service": ms_id}
        return {"skipped": f"no library handler for {ms_id}"}
    except Exception as exc:
        raise RuntimeError(f"Library configuration failed: {exc}") from exc


def _configure_livetv(ctx: JobContext) -> dict[str, Any]:
    """Configure Live TV tuners and guide sources."""
    ms_id = ctx.media_server_id()
    if not ms_id:
        return {"skipped": "no media server configured"}
    _ensure_media_server_api_key(ctx)
    try:
        mod = importlib.import_module(f"media_stack.services.apps.{ms_id}.runtime_ops")
        fn = getattr(mod, f"ensure_{ms_id}_livetv", None)
        if fn:
            fn(ctx.cfg, ctx.config_root, ctx.wait_timeout)
            return {"service": ms_id}
        return {"skipped": f"no livetv handler for {ms_id}"}
    except Exception as exc:
        raise RuntimeError(f"Live TV configuration failed: {exc}") from exc


def _configure_plugins(ctx: JobContext) -> dict[str, Any]:
    """Install media server plugins."""
    ms_id = ctx.media_server_id()
    if not ms_id:
        return {"skipped": "no media server configured"}
    _ensure_media_server_api_key(ctx)
    try:
        mod = importlib.import_module(f"media_stack.services.apps.{ms_id}.runtime_ops")
        fn = getattr(mod, f"ensure_{ms_id}_plugins", None)
        if fn:
            fn(ctx.cfg, ctx.config_root, ctx.wait_timeout)
            return {"service": ms_id}
        return {"skipped": f"no plugins handler for {ms_id}"}
    except Exception as exc:
        raise RuntimeError(f"Plugin configuration failed: {exc}") from exc


def _configure_playback(ctx: JobContext) -> dict[str, Any]:
    """Set media server playback defaults."""
    ms_id = ctx.media_server_id()
    if not ms_id:
        return {"skipped": "no media server configured"}
    _ensure_media_server_api_key(ctx)
    try:
        mod = importlib.import_module(f"media_stack.services.apps.{ms_id}.runtime_ops")
        fn = getattr(mod, f"ensure_{ms_id}_playback_defaults", None)
        if fn:
            fn(ctx.cfg, ctx.config_root, ctx.wait_timeout)
            return {"service": ms_id}
        return {"skipped": f"no playback handler for {ms_id}"}
    except Exception as exc:
        raise RuntimeError(f"Playback configuration failed: {exc}") from exc


def _configure_categories(ctx: JobContext) -> dict[str, Any]:
    """Set up download categories in torrent and usenet clients."""
    results = []
    from media_stack.api.services.registry import SERVICES
    for svc in SERVICES:
        try:
            mod = importlib.import_module(f"media_stack.services.apps.{svc.id}.runtime_ops")
            fn = getattr(mod, "setup_torrent_categories", None) or getattr(mod, "ensure_sabnzbd_categories", None)
            if fn:
                fn(ctx.cfg, ctx.config_root, ctx.wait_timeout)
                results.append(svc.id)
        except (ImportError, AttributeError):
            continue
        except Exception as exc:
            runtime_platform.log(f"[WARN] {svc.id} categories: {exc}")
    return {"configured": results}


def _noop(ctx: JobContext) -> dict[str, Any]:
    """Placeholder for composite jobs."""
    return {}


# ---------------------------------------------------------------------------
# Build the job tree
# ---------------------------------------------------------------------------

def build_bootstrap_jobs() -> Job:
    """Build the full bootstrap job tree.

    bootstrap
    ├── configure-media-server
    │   ├── configure-libraries
    │   ├── configure-livetv
    │   ├── configure-plugins
    │   └── configure-playback
    ├── configure-download-clients
    │   └── configure-categories
    ├── configure-services
    │   ├── configure-dashboard
    │   ├── configure-subtitles
    │   └── configure-request-manager
    ├── auto-indexers
    └── validate-credentials
    """
    root = Job("bootstrap", _noop)

    # Media server group
    media_server = Job("configure-media-server", _noop)
    media_server.add_sub_job(Job("configure-libraries", _configure_libraries))
    media_server.add_sub_job(Job("configure-livetv", _configure_livetv))
    media_server.add_sub_job(Job("configure-plugins", _configure_plugins))
    media_server.add_sub_job(Job("configure-playback", _configure_playback))
    root.add_sub_job(media_server)

    # Download clients group
    downloads = Job("configure-download-clients", _noop)
    downloads.add_sub_job(Job("configure-categories", _configure_categories))
    root.add_sub_job(downloads)

    return root


# ---------------------------------------------------------------------------
# Flat job registry for individual action dispatch
# ---------------------------------------------------------------------------

def get_job_registry() -> dict[str, Callable[[JobContext], dict[str, Any]]]:
    """Return flat map of job-name → handler for action dispatch."""
    return {
        "configure-libraries": _configure_libraries,
        "configure-livetv": _configure_livetv,
        "configure-plugins": _configure_plugins,
        "configure-playback": _configure_playback,
        "configure-categories": _configure_categories,
    }


def run_job(job_name: str) -> dict[str, Any]:
    """Run a single job by name."""
    registry = get_job_registry()
    handler = registry.get(job_name)
    if not handler:
        return {"error": f"Unknown job: {job_name}", "known": sorted(registry.keys())}
    ctx = JobContext()
    job = Job(job_name, handler)
    return job.run(ctx)


def run_all_media_server_jobs() -> dict[str, Any]:
    """Run all media server configuration jobs in sequence."""
    ctx = JobContext()
    jobs = build_bootstrap_jobs()
    return jobs.run(ctx)
