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

    # Profile override mappings: profile section → flat config key + sub-key
    # The profile is the user's source of truth; service YAML defaults are
    # just starting values. Profile overrides must win.
    _PROFILE_OVERRIDES: list[tuple[str, str, str | None]] = [
        # (profile_key, flat_cfg_key, sub_key_to_override_or_None_for_merge)
        ("live_tv_defaults", "jellyfin_livetv", None),       # tuners, guides, etc.
        ("jellyfin", "jellyfin_libraries", "libraries"),     # library list
    ]

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

    # Jellyfin livetv: per-app config → profile fallback
    jf_app = load_app_config("jellyfin")
    livetv_override = jf_app.get("livetv", {})
    if not livetv_override and profile:
        livetv_override = profile.get("live_tv_defaults", {})
    if livetv_override and "jellyfin_livetv" in cfg:
        target = cfg["jellyfin_livetv"]
        for k, v in livetv_override.items():
            if v is not None:
                target[k] = v

    # Jellyfin libraries: per-app config → profile fallback
    lib_override = jf_app.get("libraries")
    if not lib_override and profile:
        jf_prof = profile.get("jellyfin", {})
        if isinstance(jf_prof, dict):
            lib_override = jf_prof.get("libraries")
    if lib_override and "jellyfin_libraries" in cfg:
        cfg["jellyfin_libraries"]["libraries"] = lib_override

    # Enrich tuners/guides with required handler fields
    _enrich_livetv_entries(cfg, profile or {})

    return cfg


def _url_looks_valid(url: str) -> bool:
    """Check if a URL looks like a full, valid HTTP URL."""
    return url.startswith("https://") or url.startswith("http://")


def _extract_country_code(name: str, url: str) -> str:
    """Try to extract a 2-letter country code from a guide name or URL."""
    import re
    # From URL: epg-us.xml, epg_US.xml, /us.m3u, etc.
    m = re.search(r'[/_-]([a-zA-Z]{2})[\d]*\.(xml|m3u)', url)
    if m:
        return m.group(1).lower()
    # From name: "Germany EPG" → look up
    _NAME_TO_CODE = {
        "united states": "us", "united kingdom": "gb", "canada": "ca",
        "australia": "au", "germany": "de", "france": "fr", "spain": "es",
        "italy": "it", "brazil": "br", "mexico": "mx", "japan": "jp",
        "south korea": "kr", "india": "in", "china": "cn", "taiwan": "tw",
        "hong kong": "hk", "philippines": "ph", "thailand": "th",
        "indonesia": "id", "netherlands": "nl", "sweden": "se",
        "norway": "no", "denmark": "dk", "finland": "fi", "poland": "pl",
        "portugal": "pt", "russia": "ru", "turkey": "tr", "israel": "il",
        "uae": "ae", "chile": "cl", "south africa": "za",
        "argentina": "ar", "colombia": "co",
    }
    name_lower = name.lower().replace(" epg", "").replace(" iptv", "").strip()
    return _NAME_TO_CODE.get(name_lower, "")


def _enrich_livetv_entries(cfg: dict[str, Any], profile: dict[str, Any]) -> None:
    """Build tuner+guide lists from profile, guides-first.

    Strategy: resolve guides first via the EPG provider fallback chain.
    Only include tuners whose guide is confirmed working. This ensures
    every channel has programme data (no blank guide rows).

    Profile flag ``load_all_tuners`` (default False) overrides this and
    loads every tuner regardless of guide availability.
    """
    import hashlib

    livetv = cfg.get("jellyfin_livetv")
    if not isinstance(livetv, dict):
        return

    ltv_defaults = profile.get("live_tv_defaults", {})
    tuner_tpl = ltv_defaults.get("tuner_url_template", "https://iptv-org.github.io/iptv/countries/{code}.m3u")
    guide_tpl = ltv_defaults.get("guide_url_template", "https://iptv-epg.org/files/epg-{code}.xml")
    load_all = ltv_defaults.get("load_all_tuners", False)

    raw_tuners = livetv.get("tuners", [])
    raw_guides = livetv.get("guides", [])

    # Step 1: Resolve and validate guides first
    resolved_guides: list[dict[str, Any]] = []
    guide_country_codes: set[str] = set()  # codes that have a working guide

    for guide in raw_guides:
        if not isinstance(guide, dict):
            continue
        guide = dict(guide)  # don't mutate original
        # Map url → path
        url = guide.pop("url", None)
        if url and "path" not in guide:
            if url.startswith("/"):
                from urllib.parse import urlparse
                parsed = urlparse(guide_tpl)
                url = f"{parsed.scheme}://{parsed.netloc}{url}"
            guide["path"] = url

        path = guide.get("path", "")
        guide_name = guide.get("name", "")
        code = _extract_country_code(guide_name, path)

        # Try the EPG provider chain for the best working URL
        if code:
            try:
                from media_stack.services.epg_provider_service import resolve_guide_url
                resolved = resolve_guide_url(code)
                if resolved:
                    guide["path"] = resolved
            except Exception:
                pass

        path = guide.get("path", "")
        if not _url_looks_valid(path):
            continue

        # Fill guide defaults
        guide.setdefault("type", "xmltv")
        guide.setdefault("enrich_program_icons_from_tuner_logo", True)
        guide.setdefault("enrich_program_categories_from_tuner_groups", True)
        guide.setdefault("enable_all_tuners", False)
        if "materialized_output_path" not in guide:
            slug = hashlib.md5(path.encode()).hexdigest()[:8]
            name_slug = (guide_name or "unknown").lower().replace(" ", "-").replace("/", "-")[:20]
            guide["materialized_output_path"] = f"jellyfin/livetv-guides/{name_slug}-{slug}.xml"

        resolved_guides.append(guide)
        if code:
            guide_country_codes.add(code)

    # Step 2: Build tuner list — only include tuners with a matching guide
    resolved_tuners: list[dict[str, Any]] = []
    for tuner in raw_tuners:
        if not isinstance(tuner, dict):
            continue
        tuner = dict(tuner)
        url = tuner.get("url", "")
        if url.startswith("/"):
            from urllib.parse import urlparse
            parsed = urlparse(tuner_tpl)
            tuner["url"] = f"{parsed.scheme}://{parsed.netloc}{url}"
            url = tuner["url"]

        tuner_name = tuner.get("name", "")
        code = _extract_country_code(tuner_name, url)

        # Gate: only include if guide exists, unless load_all_tuners is set
        if not load_all and code and code not in guide_country_codes:
            runtime_platform.log(
                f"[INFO] Live TV: skipping tuner '{tuner_name}' ({code}) — no working guide. "
                "Set load_all_tuners=true in profile to override."
            )
            continue

        tuner.setdefault("type", "m3u")
        tuner.setdefault("normalize_tvg_id_suffix", True)
        tuner.setdefault("filter_to_guide_channels", True)
        tuner.setdefault("allow_hw_transcoding", True)
        if "materialized_output_path" not in tuner:
            slug = hashlib.md5(url.encode()).hexdigest()[:8]
            name_slug = (tuner_name or "unknown").lower().replace(" ", "-").replace("/", "-")[:20]
            tuner["materialized_output_path"] = f"jellyfin/livetv-tuners/{name_slug}-{slug}.m3u"

        resolved_tuners.append(tuner)

    livetv["guides"] = resolved_guides
    livetv["tuners"] = resolved_tuners
    runtime_platform.log(
        f"[INFO] Live TV: {len(resolved_guides)} guides resolved, "
        f"{len(resolved_tuners)} tuners selected "
        f"(load_all_tuners={load_all}, skipped={len(raw_tuners) - len(resolved_tuners)})"
    )


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
        runtime_platform.log(f"[JOB] {self.name}: starting")
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
                try:
                    sub.run(ctx)
                except Exception as exc:
                    runtime_platform.log(f"[WARN] {self.name}/{sub.name}: {exc}")
            elapsed = round(time.time() - t0, 1)
            runtime_platform.log(f"[OK] {self.name}: complete ({elapsed}s)")
            return {"status": "ok", "elapsed": elapsed, **result}
        except Exception as exc:
            elapsed = round(time.time() - t0, 1)
            runtime_platform.log(f"[ERR] {self.name}: {exc} ({elapsed}s)")
            return {"status": "error", "error": str(exc)[:200], "elapsed": elapsed}


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
        return {
            "status": "ok" if errors == 0 else "error",
            "elapsed": elapsed,
            "ok": ok,
            "skipped": skipped,
            "errors": errors,
            "jobs": self.results,
        }

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
            from media_stack.services.apps.jellyfin.http_preflight import run_preflight
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


def _configure_libraries(ctx: JobContext) -> dict[str, Any]:
    """Create media server libraries (Movies, TV Shows, Music, Books)."""
    return _run_media_server_handler(ctx, "libraries", "Library")


def _configure_livetv(ctx: JobContext) -> dict[str, Any]:
    """Configure Live TV tuners and guide sources.

    Pre-merges all EPG guides into a single XMLTV file with channel IDs
    rewritten to match M3U tvg-ids. This gives near-100% guide coverage
    and avoids Jellyfin's issues with multiple overlapping guide providers.
    """
    livetv = ctx.cfg.get("jellyfin_livetv", {})
    tuners = livetv.get("tuners", [])
    guides = livetv.get("guides", [])

    if not tuners:
        return {"skipped": "no tuners configured"}

    # Step 1: Pre-merge EPG guides into one file
    if guides:
        try:
            from media_stack.services.apps.jellyfin.epg_merge_service import merge_epgs

            # Collect M3U paths (materialized files on disk)
            m3u_paths = []
            for t in tuners:
                mat = t.get("materialized_output_path", "")
                if mat:
                    m3u_paths.append(str(Path(ctx.config_root) / mat))

            # Build EPG source list from guides
            epg_sources = []
            for g in guides:
                path = g.get("path", "")
                name = g.get("name", path[:40])
                if path and (path.startswith("http://") or path.startswith("https://")):
                    epg_sources.append({"url": path, "name": name})

            if m3u_paths and epg_sources:
                merged_path = str(Path(ctx.config_root) / "jellyfin" / "livetv-guides" / "merged-epg.xml")
                result = merge_epgs(
                    m3u_paths=m3u_paths,
                    epg_sources=epg_sources,
                    output_path=merged_path,
                    config_root=ctx.config_root,
                    log=runtime_platform.log,
                )

                if result.get("channels_with_programmes", 0) > 0:
                    # Replace all guides with the single merged file
                    container_path = "/config/livetv-guides/merged-epg.xml"
                    livetv["guides"] = [{
                        "type": "xmltv",
                        "path": container_path,
                        "materialized_output_path": "jellyfin/livetv-guides/merged-epg.xml",
                        "enable_all_tuners": True,
                        "enrich_program_icons_from_tuner_logo": False,
                        "enrich_program_categories_from_tuner_groups": False,
                    }]
                    runtime_platform.log(
                        f"[OK] Live TV: using merged EPG ({result['channels_with_programmes']} "
                        f"channels, {result['programmes']} programmes)"
                    )
        except Exception as exc:
            runtime_platform.log(f"[WARN] Live TV: EPG merge failed ({exc}), falling back to individual guides")

    # Step 2: Run the livetv handler with (possibly merged) config
    return _run_media_server_handler(ctx, "livetv", "Live TV")


def _configure_plugins(ctx: JobContext) -> dict[str, Any]:
    """Install media server plugins."""
    return _run_media_server_handler(ctx, "plugins", "Plugin")


def _configure_playback(ctx: JobContext) -> dict[str, Any]:
    """Set media server playback defaults."""
    return _run_media_server_handler(ctx, "playback_defaults", "Playback")


def _configure_home_rails(ctx: JobContext) -> dict[str, Any]:
    """Configure media server home screen collections/rails."""
    return _run_media_server_handler(ctx, "home_rails", "Home rails")


def _configure_auto_collections(ctx: JobContext) -> dict[str, Any]:
    """Configure auto-collections (TMDb box sets, genre collections)."""
    return _run_media_server_handler(ctx, "auto_collections_config", "Auto collections")


def _configure_prewarm(ctx: JobContext) -> dict[str, Any]:
    """Prewarm: library refresh, book/music artwork, metadata backfill."""
    return _run_media_server_handler(ctx, "prewarm", "Prewarm")


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


def build_bootstrap_jobs() -> Job:
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
    root = build_bootstrap_jobs()
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
    root = build_bootstrap_jobs()
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
    root = build_bootstrap_jobs()
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
