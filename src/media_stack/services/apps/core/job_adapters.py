"""JobContext adapters for the six core actions migrated from the
``_CORE_ACTIONS`` table into the job framework.

Each adapter is a thin wrapper that:

1. Constructs the legacy-style ``args``/``state``/``build_runner``
   collaborators that the original ``action_*`` handler expects.
2. Calls into the existing handler in
   ``media_stack.services.jobs.action_handlers``.
3. Returns ``{}`` (or a small status dict) to satisfy the job
   framework's ``Callable[[JobContext], dict]`` contract.

The legacy handlers stay where they are — the dispatch path used
by ``POST /actions/{name}`` still routes through them. This file
just wraps them for the alternate ``run_job(name)`` invocation
path used by the Job tree's "Run" button.

If a handler ever needs to do more than the legacy version
(e.g., richer status reporting), adapt it here rather than in the
legacy handler — the legacy code is still load-bearing for many
shell scripts and CI flows."""

from __future__ import annotations

import argparse
import os
from typing import Any

from media_stack.services.jobs.framework import JobContext
from media_stack.core.logging_utils import log_swallowed
# hoisted from per-method import to reduce CIRCULAR_IMPORT_RISK_RATCHET drift
# (api.services.registry is a leaf data module — no back-edge into this file)
from media_stack.api.services.registry import service_internal_url
from media_stack.api.services.health import discover_api_keys as _discover_api_keys

# hoisted from per-call os.environ to reduce OS_ENVIRON_IN_METHODS_RATCHET drift
# QBIT_USERNAME/QBIT_PASSWORD are process-wide constants — not mutated at
# runtime and not monkey-patched by tests, so reading once at import time
# is semantically equivalent to the previous per-call reads.
_QBIT_DEFAULT_USERNAME = os.environ.get("QBIT_USERNAME", "admin")
_QBIT_DEFAULT_PASSWORD = os.environ.get("QBIT_PASSWORD", "adminadmin")


def _make_servarr_http_request():
    """Build an HTTP-request callable that survives URL-base
    prefix redirects.

    Prowlarr / Sonarr / Radarr / Lidarr / Readarr all run behind a
    URL-base prefix (e.g. ``/app/prowlarr/``). GETs without the
    prefix are quietly accepted, but POST/PUT/DELETE return a 307
    redirect to the prefixed URL — and Python's ``urllib`` drops
    the POST body on a 307, so the retargeted request arrives with
    no payload and the *arr 400s.

    This wrapper disables urllib's auto-redirect, then manually
    re-issues each redirect (up to 4 hops) with the original
    method + body intact. Returns ``(status, parsed_body, raw)``.
    """
    import json as _json
    import urllib.error as _ue
    import urllib.request as _ur
    from urllib.parse import urljoin as _urljoin

    class _NoRedirect(_ur.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            return None

    opener = _ur.build_opener(_NoRedirect())

    def _http_request(base_url: str, path: str, *, api_key: str = "",
                      method: str = "GET", payload=None,
                      timeout: int = 30):
        body = None
        headers = {"Accept": "application/json"}
        if api_key:
            headers["X-Api-Key"] = api_key
        if payload is not None:
            body = _json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        current_url = base_url.rstrip("/") + path
        for _hop in range(4):
            req = _ur.Request(
                current_url, data=body, method=method, headers=headers,
            )
            try:
                with opener.open(req, timeout=timeout) as r:
                    raw = r.read()
                    try:
                        parsed = _json.loads(raw)
                    except Exception:
                        parsed = raw
                    return r.status, parsed, raw
            except _ue.HTTPError as exc:
                if exc.code in (301, 302, 303, 307, 308):
                    target = exc.headers.get("Location")
                    if target:
                        current_url = _urljoin(current_url, target)
                        continue
                try:
                    parsed = _json.loads(exc.read())
                except Exception:
                    parsed = ""
                return exc.code, parsed, str(parsed)[:300]
            except Exception as exc:
                return 599, None, str(exc)[:300]
        return 599, None, f"too many redirects from {base_url}{path}"

    return _http_request


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _default_args(ctx: JobContext) -> argparse.Namespace:
    """Build a minimal argparse.Namespace for the legacy handlers
    that take ``args``. The legacy ``_build_runner`` reads:
    ``config``, ``mode``, ``config_root``, ``wait_timeout``,
    ``auto_prowlarr_indexers``, ``env``. All come from env vars
    when absent; we mirror what the controller's CLI parser does.

    ``mode`` defaults to ``full`` — the only value that runs the
    complete pipeline. The previous default of ``compose`` was a
    bug from when this adapter assumed mode meant deploy-target;
    ``BootstrapMode`` is actually the pipeline-shape selector
    (full | media-server-prewarm | media-server-home-rails |
    media-hygiene). Without this fix every reconcile crashed
    with ``Unsupported bootstrap mode: compose``."""
    return argparse.Namespace(
        config=os.environ.get("BOOTSTRAP_CONFIG_FILE", ""),
        mode=os.environ.get("BOOTSTRAP_MODE", "full"),
        config_root=ctx.config_root,
        wait_timeout=ctx.wait_timeout,
        auto_prowlarr_indexers=False,
        env=os.environ.get("BOOTSTRAP_ENV", "prod"),
    )


def _strip_api_key_from_url(url: str) -> str:
    """Return ``url`` with any ``api_key`` / ``apikey`` query parameter
    removed.

    Exists because we migrated Jellyfin auth from the query-string
    (``?api_key=...``) to the ``X-Emby-Token`` header — callers that
    still construct a URL with the old shape would leak the credential
    into access logs even though we set the header correctly. Stripping
    is idempotent: URLs without a key parameter return unchanged.
    """
    from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

    if "?" not in url:
        return url
    parts = urlparse(url)
    kept = [
        (k, v) for (k, v) in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in {"api_key", "apikey", "api-key"}
    ]
    new_query = urlencode(kept)
    return urlunparse(parts._replace(query=new_query))


def _stub_state() -> Any:
    """Return a minimal state object compatible with the legacy
    handlers. The handlers we wrap here only call ``state.update_*``
    methods on it for telemetry; if those calls are no-ops we don't
    lose anything important."""

    class _Stub:
        def update_config(self, _data: dict) -> dict:
            return {}

        def __getattr__(self, _name: str):
            return _noop

    def _noop(*_a: Any, **_kw: Any) -> None:
        return None

    return _Stub()


# ----------------------------------------------------------------------
# Adapters
# ----------------------------------------------------------------------


def envoy_config(ctx: JobContext) -> dict:
    """Regenerate Envoy routing config + restart envoy."""
    from media_stack.services.jobs.action_handlers import action_envoy_config
    action_envoy_config(_default_args(ctx))
    return {"action": "envoy-config"}


def ingress_config(ctx: JobContext) -> dict:
    """Reconcile the K8s Ingress (rules + tls.hosts) from the runtime
    routing config. K8s-only — no-ops outside K8s by detecting
    ``K8S_NAMESPACE``. The dashboard's POST /api/routing triggers
    both envoy-config (the data-plane edge config) and this
    (the K8s control-plane Ingress rules) so a routing change
    propagates end-to-end."""
    from media_stack.api.services import k8s_ingress_sync
    result = k8s_ingress_sync.reconcile()
    return {"action": "ingress-config", **result}


def seed_runtime_overrides(ctx: JobContext) -> dict:
    """Seed ``.controller/routing-overrides.yaml`` and
    ``.controller/auth-overrides.yaml`` from the bootstrap profile
    on first run.

    Why this job exists
    -------------------
    Before v1.0.169, those override files were only written when the
    operator hit ``Save Routing`` / ``Save Auth`` in the dashboard.
    On a clean deploy with no dashboard interaction:

      - ``get_routing()`` fell back to bundled defaults (``.local``
        gateway host) because the override file didn't exist.
      - ``ingress-config`` silently returned ``skipped`` because its
        routing-config lookup went through the same merged view and
        the operator's real hostnames were nowhere to be found.
      - A pod restart would re-run bootstrap with the SAME empty
        state — so "clean deploy" was only ever working if someone
        had clicked Save once. Reproducibility was a lie.

    This job seeds the overrides from the profile at pre_bootstrap
    time so the merged view is complete on first run. Idempotent — if
    the override file already exists (operator saved something via
    the dashboard) we leave it alone; the dashboard is the authority
    for everything the operator configured after install.

    Applies on both runtimes because compose has the same underlying
    shape (override files on the PVC feed ``get_routing()``) even if
    the "patched live" failure is less visible there.
    """
    import os
    import yaml
    from pathlib import Path
    from media_stack.api.services._resolve import resolve_profile_path

    config_root = Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
    overrides_dir = config_root / ".controller"
    overrides_dir.mkdir(parents=True, exist_ok=True)

    profile_path_str = resolve_profile_path(
        os.environ.get("BOOTSTRAP_PROFILE_FILE", "")
    )
    if not profile_path_str:
        return {"action": "seed-runtime-overrides",
                "skipped": True,
                "reason": "BOOTSTRAP_PROFILE_FILE not resolvable"}
    try:
        profile = yaml.safe_load(
            Path(profile_path_str).read_text(encoding="utf-8")
        ) or {}
    except Exception as exc:
        return {"action": "seed-runtime-overrides",
                "error": f"profile read failed: {exc}"[:120]}

    created: list[str] = []
    kept: list[str] = []

    # Routing overrides — seed from profile.routing. Dashboard saves
    # land in the same file, so the two never fight: seed writes the
    # file ONCE (skipped if already present), dashboard writes every
    # time the operator clicks Save.
    routing_path = overrides_dir / "routing-overrides.yaml"
    if routing_path.is_file():
        kept.append("routing-overrides.yaml")
    else:
        routing = profile.get("routing") or {}
        if routing:
            try:
                routing_path.write_text(
                    yaml.dump({"routing": routing},
                              default_flow_style=False, sort_keys=False),
                    encoding="utf-8",
                )
                created.append("routing-overrides.yaml")
            except Exception as exc:
                return {"action": "seed-runtime-overrides",
                        "error": f"write routing-overrides failed: {exc}"[:120]}

    # Auth overrides — same pattern, keyed off profile.auth. When the
    # profile has no auth section (pure LAN deploy) we skip this rather
    # than writing an empty stub, since the override-presence is also
    # used by probes as "operator configured auth".
    auth_path = overrides_dir / "auth-overrides.yaml"
    if auth_path.is_file():
        kept.append("auth-overrides.yaml")
    else:
        auth = profile.get("auth") or {}
        if auth:
            try:
                auth_path.write_text(
                    yaml.dump({"auth": auth},
                              default_flow_style=False, sort_keys=False),
                    encoding="utf-8",
                )
                created.append("auth-overrides.yaml")
            except Exception as exc:
                return {"action": "seed-runtime-overrides",
                        "error": f"write auth-overrides failed: {exc}"[:120]}

    return {"action": "seed-runtime-overrides",
            "created": created, "kept": kept}


def validate_credentials(ctx: JobContext) -> dict:
    """Probe service admin credentials and auto-sync passwords for
    services that fail."""
    from media_stack.services.jobs.action_handlers import (
        action_validate_credentials,
    )
    action_validate_credentials()
    return {"action": "validate-credentials"}


def restart_apps(ctx: JobContext) -> dict:
    """Restart all apps to pick up config changes."""
    from media_stack.services.jobs.action_handlers import action_restart_apps
    from media_stack.services.jobs.controller_handlers import (
        _load_handler_specs, _run_handler_specs,
    )
    action_restart_apps(
        _default_args(ctx), _stub_state(),
        _load_handler_specs, _run_handler_specs,
    )
    return {"action": "restart-apps"}


def discover_indexers(ctx: JobContext) -> dict:
    """Run auto-indexer discovery (indexer phase only).

    Scopes ``MEDIA_STACK_HTTP_RETRY_ATTEMPTS`` to 1 for the
    duration. Discovery probes ~70 indexers; many time out
    (CloudFlare-protected, dead, slow). With the default 3
    attempts at ~10s timeout each, a dead indexer wastes 30s.
    70 × 30s = 35 min worst case — long enough to blow past
    the bootstrap action timeout. Single-attempt during discovery
    means dead indexers cost ~10s instead of ~30s; the ones that
    work succeed on first try regardless. The reputation system
    re-tries skipped indexers on subsequent runs."""
    import os as _os
    prev = _os.environ.get("MEDIA_STACK_HTTP_RETRY_ATTEMPTS")
    _os.environ["MEDIA_STACK_HTTP_RETRY_ATTEMPTS"] = "1"
    try:
        from media_stack.services.jobs.action_handlers import (
            action_discover_indexers,
        )
        from media_stack.services.jobs.controller_runner import _build_runner
        action_discover_indexers(_default_args(ctx), _build_runner)
        return {"action": "discover-indexers"}
    finally:
        if prev is None:
            _os.environ.pop("MEDIA_STACK_HTTP_RETRY_ATTEMPTS", None)
        else:
            _os.environ["MEDIA_STACK_HTTP_RETRY_ATTEMPTS"] = prev


def tag_indexers_for_apps(ctx: JobContext) -> dict:
    """Probe each Prowlarr indexer per *arr app, then tag both
    indexers and Prowlarr's app entries so ApplicationIndexerSync
    only pushes indexers that actually return results for that
    app's content type. Stops the "Radarr stays at 0 indexers
    even though Prowlarr has 33 movie-capable ones" failure mode
    where many anime/TV indexers claim movie capability but probe
    empty for movies, and Radarr rejects them on add."""
    from media_stack.services.apps.prowlarr.indexer_app_match import (
        apply_indexer_app_tags,
    )
    import json as _json
    import urllib.request as _ur
    import urllib.error as _ue

    _http_request = _make_servarr_http_request()

    def _log(msg: str) -> None:
        from media_stack.services.jobs.controller_runner import (
            runtime_platform,
        )
        runtime_platform.log(msg)

    prowlarr_url = ctx.service_url("prowlarr")
    prowlarr_key = ctx.api_key("prowlarr")
    if not prowlarr_url or not prowlarr_key:
        _log("[WARN] tag-indexers-for-apps: Prowlarr unreachable, skipping")
        return {"action": "tag-indexers-for-apps", "skipped": True}
    summary = apply_indexer_app_tags(
        prowlarr_url=prowlarr_url,
        prowlarr_api_key=prowlarr_key,
        http_request=_http_request,
        log=_log,
    )
    return {"action": "tag-indexers-for-apps", "summary": summary}


def reset_prowlarr_app_mappings(ctx: JobContext) -> dict:
    """Reconcile Prowlarr's ``ApplicationIndexerMapping`` table
    against each *arr's actual indexer list.

    Failure mode this fixes:
    Prowlarr tracks "what I've already pushed to each *arr" in a
    SQLite table called ``ApplicationIndexerMapping``. In
    ``addOnly`` syncLevel mode (the v1.0.110 Fix C default),
    Prowlarr SKIPS any indexer-to-app pair already in this table,
    even if the *arr's actual DB has been wiped. Result: the *arr
    sits at 0 indexers while Prowlarr's "sync ok" log claims
    everything is fine. (Discovered v1.0.124 in live diagnosis.)

    Reconciliation: for each *arr, if its actual indexer count is 0
    but Prowlarr has mappings for it, DELETE those mapping rows so
    the next ApplicationIndexerSync pushes fresh.

    Implemented as a thin SQLite-based reset because the Prowlarr
    REST API has no "reset mappings" endpoint. The table is purely
    Prowlarr's bookkeeping; deleting rows is safe and idempotent.
    """
    import sqlite3 as _sql
    from pathlib import Path as _P
    config_root = _P(ctx.config_root if hasattr(ctx, "config_root") else "/srv-config")
    prowlarr_db = config_root / "prowlarr" / "prowlarr.db"
    if not prowlarr_db.is_file():
        return {"action": "reset-prowlarr-app-mappings", "skipped": True,
                "reason": f"prowlarr.db not found at {prowlarr_db}"}
    cleared: dict[str, int] = {}
    try:
        conn = _sql.connect(str(prowlarr_db))
        try:
            for app in ("Sonarr", "Radarr", "Lidarr", "Readarr"):
                arr_url = ctx.service_url(app.lower())
                arr_key = ctx.api_key(app.lower())
                if not arr_url or not arr_key:
                    continue
                # Get the *arr's actual indexer count.
                http = _make_servarr_http_request()
                ver_path = "/api/v1/indexer" if app in ("Lidarr", "Readarr") else "/api/v3/indexer"
                status, body, _ = http(arr_url, ver_path, api_key=arr_key)
                actual = len(body) if status == 200 and isinstance(body, list) else None
                if actual is None or actual > 0:
                    continue  # *arr not reachable, or already has indexers
                # *arr has 0 — clear Prowlarr's mappings for it.
                cur = conn.execute(
                    "DELETE FROM ApplicationIndexerMapping WHERE AppId IN "
                    "(SELECT Id FROM Applications WHERE Name = ?)", (app,),
                )
                cleared[app] = cur.rowcount
            conn.commit()
        finally:
            conn.close()
    except _sql.Error as exc:
        return {"action": "reset-prowlarr-app-mappings",
                "error": f"sqlite: {exc}"}
    return {"action": "reset-prowlarr-app-mappings", "cleared": cleared}


def push_indexers(ctx: JobContext) -> dict:
    """Trigger indexer-manager ApplicationIndexerSync."""
    from media_stack.services.jobs.action_handlers import action_push_indexers
    from media_stack.services.jobs.controller_runner import _build_runner
    action_push_indexers(_default_args(ctx), _build_runner)
    return {"action": "push-indexers"}


def run_media_hygiene(ctx: JobContext) -> dict:
    """Auto-cleanup pass: prunes stalled / errored / orphaned
    downloads from qBit + the *arr queues. Bounded to 25 deletes
    per run.

    Auto-fired hourly by the controller's scheduler so end users
    never need to log into qBit to clean up — the user's only
    interaction with the stack should be Jellyfin.
    """
    from media_stack.services.apps.servarr.runtime.hygiene_ops import (
        run_media_hygiene as _hygiene,
    )
    cfg = ctx.cfg or {}
    arr_apps = []
    app_keys: dict[str, str] = {}
    for name in ("sonarr", "radarr", "lidarr", "readarr"):
        url = ctx.service_url(name)
        key = ctx.api_key(name)
        if not url or not key:
            continue
        arr_apps.append({
            "name": name.capitalize(),
            "implementation": name.capitalize(),
            "url": url,
        })
        app_keys[name.capitalize()] = key
    qbit_cfg = (cfg.get("qbittorrent") or cfg.get("download_clients", {}).get("qbittorrent")
                or {})
    try:
        result = _hygiene(
            cfg=cfg,
            config_root=ctx.config_root,
            arr_apps=arr_apps,
            app_keys=app_keys,
            qbit_cfg=qbit_cfg,
            qb_username=str(qbit_cfg.get("username") or "admin"),
            qb_password=str(qbit_cfg.get("password") or ""),
        ) or {}
    except Exception as exc:
        return {"action": "run-media-hygiene", "error": str(exc)[:200]}
    return {"action": "run-media-hygiene", "summary": result}


def ensure_bazarr_language_profile(ctx: JobContext) -> dict:
    """Bring Bazarr to a usable OTB state in one POST:

      1. Enable English (``languages-enabled=en``).
      2. Create a default English language profile if none exists.
      3. Set that profile as the default for new Sonarr series and
         Radarr movies (``general-serie_default_profile`` /
         ``general-movie_default_profile``). Without this, every
         new show/movie comes in with profile=None and downloads
         no subtitles even though a profile exists.
      4. Replace the provider list with a curated OTB set:
         ``opensubtitlescom``, ``podnapisi``, ``gestdown`` (TV),
         ``yifysubtitles`` (movies, pairs with YTS), and
         ``embeddedsubtitles`` (extracts subtitle tracks already
         in the .mkv — zero network cost). Drops ``addic7ed``
         which has anti-scrape issues; Gestdown is its modern
         replacement.

    Bazarr's settings endpoint is Flask + flask_restx. Form-
    encoded ``POST /api/system/settings`` is the only effective
    write path — the JSON profile-list endpoint is read-only.
    Array-shaped settings (``enabled_providers``,
    ``languages-enabled``) use repeated form keys (urlencode with
    ``doseq=True``); ``languages-profiles`` is a single field with
    a JSON-encoded string value.

    Idempotent: skips profile creation if any profile already
    exists, but always re-asserts defaults + provider list (so
    drift is corrected on every run).
    """
    import json as _json
    import urllib.parse as _up
    import urllib.request as _ur
    import urllib.error as _ue
    url = ctx.service_url("bazarr")
    key = ctx.api_key("bazarr")
    if not url or not key:
        return {"action": "ensure-bazarr-language-profile", "skipped": "no url/key"}
    base = f"{url.rstrip('/')}/api"

    try:
        req = _ur.Request(
            f"{base}/system/languages/profiles",
            headers={"X-Api-Key": key},
        )
        with _ur.urlopen(req, timeout=10) as r:
            existing = _json.loads(r.read())
    except Exception as exc:
        return {"action": "ensure-bazarr-language-profile",
                "error": f"profile list failed: {str(exc)[:80]}"}

    profile_action = "skipped"
    profile_id = 1
    if isinstance(existing, list) and existing:
        # Use the first existing profile's id for the default-profile
        # field. Operator may have customised — don't clobber.
        first = existing[0]
        if isinstance(first, dict) and first.get("profileId") is not None:
            profile_id = int(first["profileId"])
    else:
        profile_action = "created"

    # ``items.language`` shape varies across Bazarr versions: some
    # accept the bare code2 string, others want ``{"code2": "en", ...}``.
    # The bare string has worked reliably since v1.5; keep using it.
    profile_payload = {
        "profileId": profile_id,
        "name": "English",
        "items": [{
            "id": 1, "language": "en",
            "audio_exclude": "False", "hi": "False", "forced": "False",
        }],
        "cutoff": None,
        "originalFormat": None,
        "mustContain": [],
        "mustNotContain": [],
        "tag": None,
    }

    providers = [
        "opensubtitlescom",  # broad: movies + TV
        "podnapisi",         # broad: secondary
        "gestdown",          # TV (modern Addic7ed replacement)
        "yifysubtitles",     # movies (pairs with YTS releases)
        "embeddedsubtitles", # zero-network: extracts existing .mkv tracks
    ]

    # Bazarr's default-profile feature has TWO knobs per media type:
    # ``_default_enabled`` (bool toggle) and ``_default_profile`` (id).
    # The ID alone does nothing — the UI shows the toggle as OFF and
    # Bazarr doesn't auto-assign the profile to new content. Both
    # must be set. (v1.0.146 — caught when the UI didn't reflect the
    # change despite the API reporting the ID value.)
    form_pairs: list[tuple[str, str]] = [
        ("languages-enabled", "en"),
        ("languages-profiles", _json.dumps([profile_payload])),
        ("settings-general-serie_default_enabled", "true"),
        ("settings-general-serie_default_profile", str(profile_id)),
        ("settings-general-movie_default_enabled", "true"),
        ("settings-general-movie_default_profile", str(profile_id)),
    ]
    for p in providers:
        form_pairs.append(("settings-general-enabled_providers", p))

    # *arr integration. Bazarr's UI shows "Use Sonarr / Radarr — not
    # configured" on a fresh install because ``use_sonarr``/``use_radarr``
    # default to False, ip defaults to 127.0.0.1, and apikey is empty.
    # Set hostname (docker DNS), port, URL base (matching the
    # ``/app/<app>/`` prefix the preflight persisted), and API key.
    # Without this, Bazarr never polls Sonarr/Radarr for episodes /
    # movies, so no subtitles get fetched even with a default profile
    # and providers configured. (v1.0.146.)
    arr_integrations = {
        "sonarr":  (8989, "/app/sonarr"),
        "radarr":  (7878, "/app/radarr"),
    }
    integrations_added: list[str] = []
    for app, (port, base_url) in arr_integrations.items():
        arr_key = ctx.api_key(app)
        if not arr_key:
            continue
        form_pairs.extend([
            (f"settings-general-use_{app}", "true"),
            (f"settings-{app}-ip", app),
            (f"settings-{app}-port", str(port)),
            (f"settings-{app}-base_url", base_url),
            (f"settings-{app}-apikey", arr_key),
            (f"settings-{app}-ssl", "false"),
        ])
        integrations_added.append(app)

    body = _up.urlencode(form_pairs).encode()
    try:
        req = _ur.Request(
            f"{base}/system/settings",
            data=body, method="POST",
            headers={
                "X-Api-Key": key,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with _ur.urlopen(req, timeout=15) as r:
            status = r.status
    except _ue.HTTPError as exc:
        return {"action": "ensure-bazarr-language-profile",
                "error": f"settings POST failed (HTTP {exc.code}): {exc.read()[:120].decode(errors='replace')}"}
    except Exception as exc:
        return {"action": "ensure-bazarr-language-profile",
                "error": str(exc)[:120]}

    # Seed the Jellyfin-side Bazarr plugin config so the in-Jellyfin
    # "Edit Subtitles" UI can talk to Bazarr without the user having
    # to open Jellyfin → Plugins → Bazarr and fill in URL+API-key.
    # Plugin: enoch85/bazarr-jellyfin (GPLv3). The plugin config
    # lives at ``<jellyfin_config>/plugins/configurations/Bazarr.xml``
    # and Jellyfin reads it on plugin load. Writing it pre-install
    # is safe — the plugin picks it up the first time it loads.
    plugin_config_written = False
    try:
        from pathlib import Path as _Path
        config_root = _Path(ctx.config_root)
        # Jellyfin's /config bind-mount is rooted at /srv-config/jellyfin/
        # (not /srv-config/jellyfin/config/) — the container sees
        # /config/plugins/configurations/ which maps to this host path.
        config_dir = config_root / "jellyfin" / "plugins" / "configurations"
        config_dir.mkdir(parents=True, exist_ok=True)
        xml = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<PluginConfiguration xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xmlns:xsd="http://www.w3.org/2001/XMLSchema">\n'
            f'  <BazarrUrl>{url.rstrip("/")}</BazarrUrl>\n'
            f'  <BazarrApiKey>{key}</BazarrApiKey>\n'
            '  <EnableForMovies>true</EnableForMovies>\n'
            '  <EnableForEpisodes>true</EnableForEpisodes>\n'
            '  <SearchTimeoutSeconds>25</SearchTimeoutSeconds>\n'
            '</PluginConfiguration>\n'
        )
        # Jellyfin names plugin config files by the assembly name,
        # not the display name. Bazarr plugin's assembly is
        # ``Jellyfin.Plugin.Bazarr``. Writing to ``Bazarr.xml``
        # silently does nothing — the plugin keeps its in-memory
        # defaults and the UI shows ``http://localhost:6767`` /
        # empty API key. Verified via
        # ``GET /Plugins`` → ``configurationFileName``. (v1.0.146.)
        (config_dir / "Jellyfin.Plugin.Bazarr.xml").write_text(xml, encoding="utf-8")
        # Clean up any previous stray file from the v1.0.146 first
        # cut, before we knew the right filename.
        stray = config_dir / "Bazarr.xml"
        if stray.exists():
            stray.unlink()
        plugin_config_written = True
    except Exception:
        # Non-fatal — plugin still works, user can fill in the fields
        # manually via Jellyfin → Dashboard → Plugins → Bazarr.
        pass

    return {
        "action": "ensure-bazarr-language-profile",
        "profile": f"English (en) — {profile_action} (id={profile_id})",
        "default_for_new": "series + movies",
        "providers": providers,
        "arr_integrations": integrations_added,
        "jellyfin_plugin_config": "written" if plugin_config_written else "skipped",
        "status": status,
    }


def _mass_search_qbit_active_count(ctx: JobContext) -> int | None:
    """Return the count of currently-downloading qBit torrents, or
    None if qBit can't be reached. Used by the adaptive scheduler
    to decide whether to skip a search tick (qBit is busy)."""
    import urllib.parse as _up
    import urllib.request as _ur
    qb_user = _QBIT_DEFAULT_USERNAME
    qb_pass = _QBIT_DEFAULT_PASSWORD
    base = (ctx.service_url("qbittorrent") or service_internal_url("qbittorrent")).rstrip("/")
    cj = __import__("http.cookiejar", fromlist=["CookieJar"]).CookieJar()
    opener = _ur.build_opener(_ur.HTTPCookieProcessor(cj))
    try:
        opener.open(_ur.Request(
            f"{base}/api/v2/auth/login",
            data=_up.urlencode({"username": qb_user, "password": qb_pass}).encode(),
        ), timeout=5)
        import json as _json
        with opener.open(
            f"{base}/api/v2/torrents/info?filter=active", timeout=5,
        ) as r:
            return len(_json.loads(r.read()))
    except Exception:
        return None


def mass_search_throttled(ctx: JobContext) -> dict:
    """Adaptive "search every monitored missing item" pass.

    The hourly scheduler fires this every tick. The adapter then
    decides whether to:

      a) **Skip** — library is healthy AND qBit is actively
         downloading. No reason to add more search load; existing
         downloads will populate the library.

      b) **Run lazy** — library is healthy but qBit is idle. Pace
         dispatch at ``MASS_SEARCH_DELAY_SECONDS`` (default 2s)
         so indexers don't get hammered.

      c) **Run aggressive** — library is "thin" (fewer than
         ``MASS_SEARCH_THIN_THRESHOLD`` imported files, default 25).
         Pace at ``MASS_SEARCH_THIN_DELAY_SECONDS`` (default 0.5s)
         so first-hour impressions land fast. The 429 risk is
         acceptable here because there's NO content yet anyway.

    Why hourly + adaptive instead of fixed cadence: a fresh install
    needs urgency (user sees nothing); a healthy install with
    hundreds of in-flight downloads doesn't (search load is wasted).
    Self-throttles without operator tuning. (v1.0.148.)
    """
    import json as _json
    import time as _t
    import urllib.request as _ur
    import urllib.error as _ue
    healthy_delay = float(os.environ.get("MASS_SEARCH_DELAY_SECONDS", "2.0"))
    thin_delay = float(os.environ.get("MASS_SEARCH_THIN_DELAY_SECONDS", "0.5"))
    thin_threshold = int(os.environ.get("MASS_SEARCH_THIN_THRESHOLD", "25"))
    max_items = int(os.environ.get("MASS_SEARCH_MAX_ITEMS", "200"))

    # ---- Adaptive decision: count imported files + qBit activity.
    imported_count = 0
    for _app, _ver, _stat_field in (
        ("sonarr", "v3", "episodeFileCount"),
        ("radarr", "v3", "hasFile"),
    ):
        _u = ctx.service_url(_app); _k = ctx.api_key(_app)
        if not _u or not _k:
            continue
        try:
            _path = "series" if _app == "sonarr" else "movie"
            _req = _ur.Request(
                f"{_u.rstrip('/')}/api/{_ver}/{_path}",
                headers={"X-Api-Key": _k},
            )
            with _ur.urlopen(_req, timeout=10) as _r:
                _items = _json.loads(_r.read())
            if _app == "sonarr":
                imported_count += sum(
                    int((s.get("statistics") or {}).get("episodeFileCount", 0))
                    for s in _items if isinstance(s, dict)
                )
            else:
                imported_count += sum(
                    1 for m in _items if isinstance(m, dict) and m.get("hasFile")
                )
        except Exception as exc:
            # Per-app failure — one *arr unreachable or returning junk
            # shouldn't abort the mass-search decision for the rest.
            # Keep imported_count unchanged for this app; the thin-
            # library check stays conservative.
            log_swallowed(exc)

    qbit_active = _mass_search_qbit_active_count(ctx)
    is_thin = imported_count < thin_threshold

    # Skip when healthy AND qBit is busy. Single condition keeps the
    # logic legible — both factors must be present to justify a noop.
    if not is_thin and qbit_active is not None and qbit_active > 0:
        return {
            "action": "mass-search-throttled",
            "skipped": "healthy library + qBit active",
            "imported_files": imported_count,
            "qbit_active": qbit_active,
        }

    delay = thin_delay if is_thin else healthy_delay
    mode = "aggressive (thin)" if is_thin else "lazy (healthy idle)"
    arr_specs = [
        # (app, api_ver, list_path, list_filter, search_cmd, id_field)
        ("sonarr",  "v3", "/series", lambda s: s.get("monitored") and (s.get("statistics") or {}).get("episodeFileCount", 0) == 0, "SeriesSearch", "seriesId"),
        ("radarr",  "v3", "/movie",  lambda m: m.get("monitored") and not m.get("hasFile"), "MoviesSearch", "movieIds"),
    ]
    summary: dict[str, dict[str, int]] = {}
    for app, ver, list_path, fn, cmd, id_field in arr_specs:
        url = ctx.service_url(app)
        key = ctx.api_key(app)
        if not url or not key:
            summary[app] = {"skipped": 1}
            continue
        # *arrs serve at ``/app/<app>/`` URL base (set by preflight).
        # Hitting the bare ``/api/...`` returns a 307 redirect, and
        # urllib drops the POST body on 307 — the search command
        # arrives empty and the *arr returns 400. Always include the
        # prefix. (Same bug pattern as ensure_arr_jellyfin_notifier.)
        api_base = f"{url.rstrip('/')}/app/{app}/api/{ver}"
        try:
            req = _ur.Request(
                f"{api_base}{list_path}",
                headers={"X-Api-Key": key},
            )
            with _ur.urlopen(req, timeout=15) as r:
                items = _json.loads(r.read())
        except Exception as exc:
            summary[app] = {"list_error": str(exc)[:60]}
            continue
        targets = [i for i in items if isinstance(i, dict) and fn(i)][:max_items]
        fired = 0
        errors = 0
        for item in targets:
            iid = item.get("id")
            if iid is None:
                continue
            payload = (
                {"name": cmd, id_field: [int(iid)]}
                if id_field.endswith("s") else
                {"name": cmd, id_field: int(iid)}
            )
            cmd_req = _ur.Request(
                f"{api_base}/command",
                data=_json.dumps(payload).encode(), method="POST",
                headers={"X-Api-Key": key, "Content-Type": "application/json"},
            )
            try:
                with _ur.urlopen(cmd_req, timeout=10):
                    fired += 1
            except Exception:
                errors += 1
            _t.sleep(delay)
        summary[app] = {"fired": fired, "errors": errors, "candidates": len(targets)}
    return {
        "action": "mass-search-throttled",
        "mode": mode,
        "imported_files": imported_count,
        "qbit_active": qbit_active,
        "delay_seconds": delay,
        "max_items": max_items,
        "summary": summary,
    }


def ensure_qbittorrent_categories(ctx: JobContext) -> dict:
    """Make sure qBit has the four content categories (movies, tv,
    music, books) with savePath under ``/data/torrents/completed/``.
    Idempotent — qBit's createCategory is a no-op when the category
    already exists with the same savePath.
    """
    import urllib.parse as _up
    import urllib.request as _ur
    import urllib.error as _ue
    qb_user = _QBIT_DEFAULT_USERNAME
    qb_pass = _QBIT_DEFAULT_PASSWORD
    base = (ctx.service_url("qbittorrent") or service_internal_url("qbittorrent")).rstrip("/")
    # Login first — qBit's API needs a session cookie even for the
    # category endpoints.
    cj = __import__("http.cookiejar", fromlist=["CookieJar"]).CookieJar()
    opener = _ur.build_opener(_ur.HTTPCookieProcessor(cj))
    try:
        opener.open(_ur.Request(
            f"{base}/api/v2/auth/login",
            data=_up.urlencode({"username": qb_user, "password": qb_pass}).encode(),
        ), timeout=10)
    except Exception as exc:
        return {"action": "ensure-qbittorrent-categories",
                "error": f"login failed: {str(exc)[:80]}"}

    desired = {
        "movies": "/data/torrents/completed/movies",
        "tv":     "/data/torrents/completed/tv",
        "music":  "/data/torrents/completed/music",
        "books":  "/data/torrents/completed/books",
    }
    created: list[str] = []
    skipped: list[str] = []
    for cat, save_path in desired.items():
        body = _up.urlencode({"category": cat, "savePath": save_path}).encode()
        try:
            opener.open(_ur.Request(
                f"{base}/api/v2/torrents/createCategory", data=body,
            ), timeout=10)
            created.append(cat)
        except _ue.HTTPError as exc:
            # qBit returns 409 when the category already exists.
            if exc.code == 409:
                skipped.append(cat)
            else:
                return {"action": "ensure-qbittorrent-categories",
                        "error": f"{cat}: HTTP {exc.code}"}
        except Exception as exc:
            return {"action": "ensure-qbittorrent-categories",
                    "error": f"{cat}: {str(exc)[:60]}"}
    return {"action": "ensure-qbittorrent-categories",
            "created": created, "skipped": skipped}


def ensure_arr_download_client(ctx: JobContext) -> dict:
    """Make sure each *arr has qBit configured as an enabled download
    client with the right category. Idempotent — updates the
    existing entry if it differs, creates if missing.

    Field name varies by *arr: ``tvCategory`` for Sonarr,
    ``movieCategory`` for Radarr, ``musicCategory`` for Lidarr,
    ``bookCategory`` for Readarr.
    """
    import json as _json
    import urllib.request as _ur
    import urllib.error as _ue
    qb_user = _QBIT_DEFAULT_USERNAME
    qb_pass = _QBIT_DEFAULT_PASSWORD
    arr_specs = [
        ("sonarr",  "v3", "tvCategory",     "tv"),
        ("radarr",  "v3", "movieCategory",  "movies"),
        ("lidarr",  "v1", "musicCategory",  "music"),
        ("readarr", "v1", "bookCategory",   "books"),
    ]
    summary: dict[str, str] = {}
    for app, ver, cat_field, cat_value in arr_specs:
        url = ctx.service_url(app)
        key = ctx.api_key(app)
        if not url or not key:
            summary[app] = "skipped (no url/key)"
            continue
        base = f"{url.rstrip('/')}/app/{app}/api/{ver}/downloadclient"
        H = {"X-Api-Key": key, "Content-Type": "application/json"}
        try:
            req = _ur.Request(base, headers={"X-Api-Key": key})
            with _ur.urlopen(req, timeout=10) as r:
                existing = _json.loads(r.read())
        except Exception as exc:
            summary[app] = f"list error: {str(exc)[:60]}"
            continue
        match = next(
            (c for c in (existing or [])
             if c.get("implementation") == "QBittorrent"),
            None,
        )
        payload_fields = [
            {"name": "host",      "value": "qbittorrent"},
            {"name": "port",      "value": 8080},
            {"name": "useSsl",    "value": False},
            {"name": "urlBase",   "value": ""},
            {"name": "username",  "value": qb_user},
            {"name": "password",  "value": qb_pass},
            {"name": cat_field,   "value": cat_value},
        ]
        payload = {
            "name": "qBittorrent",
            "implementation": "QBittorrent",
            "configContract": "QBittorrentSettings",
            "enable": True,
            "priority": 1,
            "removeCompletedDownloads": False,
            "removeFailedDownloads": True,
            "fields": payload_fields,
        }
        try:
            if match:
                payload["id"] = match.get("id")
                _ur.urlopen(_ur.Request(
                    f"{base}/{match['id']}", data=_json.dumps(payload).encode(),
                    method="PUT", headers=H,
                ), timeout=10)
                summary[app] = f"updated (id={match['id']})"
            else:
                _ur.urlopen(_ur.Request(
                    base, data=_json.dumps(payload).encode(),
                    method="POST", headers=H,
                ), timeout=10)
                summary[app] = "created"
        except _ue.HTTPError as exc:
            summary[app] = f"HTTP {exc.code}: {exc.read()[:60].decode(errors='replace')}"
        except Exception as exc:
            summary[app] = str(exc)[:60]
    return {"action": "ensure-arr-download-client", "summary": summary}


def ensure_jellyfin_libraries(ctx: JobContext) -> dict:
    """Make sure Jellyfin has Movies, TV Shows, Music, Books libraries
    pointing at ``/media/<category>/``. Idempotent — skips libraries
    that already exist with the right path; creates missing ones via
    ``POST /Library/VirtualFolders``.

    Jellyfin's stores its API key in SQLite — re-uses the controller's
    discovery flow via the JellyfinApiKeyDb helper.
    """
    import json as _json
    import urllib.parse as _up
    import urllib.request as _ur
    keys = _discover_api_keys()
    jf_key = keys.get("jellyfin", "")
    if not jf_key:
        return {"action": "ensure-jellyfin-libraries", "skipped": "no jellyfin key"}
    base = service_internal_url("jellyfin")

    desired = [
        ("Movies",   "movies",  "/media/movies", "Tmdb"),
        ("TV Shows", "tvshows", "/media/tv",     "Tmdb"),
        ("Music",    "music",   "/media/music",  "MusicBrainz"),
        ("Books",    "books",   "/media/books",  "Open Library"),
    ]
    # Jellyfin 10.11 honours ``X-Emby-Token`` in place of the legacy
    # ``?api_key=`` query parameter. Moving the credential into a
    # header keeps it out of access logs, proxy telemetry, and the
    # browser's URL autofill history — all of which historically
    # ingest the query-string variant verbatim.
    try:
        req = _ur.Request(
            _strip_api_key_from_url(f"{base}/Library/VirtualFolders"),
            headers={"Accept": "application/json",
                     "X-Emby-Token": jf_key},
        )
        with _ur.urlopen(req, timeout=10) as r:
            existing = _json.loads(r.read())
    except Exception as exc:
        return {"action": "ensure-jellyfin-libraries",
                "error": f"list failed: {str(exc)[:80]}"}

    have = {
        (l.get("Name"), l.get("CollectionType"))
        for l in (existing or [])
    }
    added: list[str] = []
    skipped: list[str] = []
    for name, ctype, path, _meta in desired:
        if (name, ctype) in have:
            skipped.append(name)
            continue
        # Keep the non-credential params in the URL; the auth goes
        # via ``X-Emby-Token`` so nothing sensitive leaks into logs.
        params = _up.urlencode({
            "name": name,
            "collectionType": ctype,
            "paths": path,
            "refreshLibrary": "false",
        })
        try:
            _ur.urlopen(_ur.Request(
                f"{base}/Library/VirtualFolders?{params}",
                method="POST",
                headers={"X-Emby-Token": jf_key},
            ), timeout=15)
            added.append(name)
        except Exception as exc:
            return {"action": "ensure-jellyfin-libraries",
                    "error": f"{name}: {str(exc)[:60]}"}
    return {"action": "ensure-jellyfin-libraries",
            "added": added, "skipped": skipped}


def ensure_sonarr_seed_series(ctx: JobContext) -> dict:
    """Add the seed-series titles + the popular-TV CustomImport list
    to Sonarr if missing. Idempotent — Sonarr's series API skips
    duplicates by tvdbId; the CustomImport is keyed on name.

    Why this exists as its own job (v1.0.146): the seed-series
    side-effect previously fired only as a runtime hook inside the
    main bootstrap pipeline. ``compose down -v`` wiped Sonarr's DB
    and the next ``up`` rebuilt it minus the seed series unless the
    full pipeline ran cleanly. Promoting to a dedicated ensure-*
    job means it's observable, re-runnable on demand, and probed
    by the promises registry.
    """
    import json as _json
    import urllib.parse as _up
    import urllib.request as _ur
    url = ctx.service_url("sonarr")
    key = ctx.api_key("sonarr")
    if not url or not key:
        return {"action": "ensure-sonarr-seed-series", "skipped": "no url/key"}

    base = f"{url.rstrip('/')}/app/sonarr/api/v3"
    H = {"X-Api-Key": key, "Content-Type": "application/json"}

    cfg = ctx.cfg or {}
    seed = cfg.get("sonarr_seed_series") or {}
    series_names = [
        s.strip() for s in (seed.get("series") or [])
        if isinstance(s, str) and s.strip()
    ]
    if not series_names:
        return {"action": "ensure-sonarr-seed-series",
                "skipped": "no series names in contracts/defaults/arr.yaml"}

    monitor = str(seed.get("monitor") or "firstSeason")
    season_folder = bool(seed.get("season_folder", True))

    try:
        qps = _json.loads(_ur.urlopen(_ur.Request(
            f"{base}/qualityprofile", headers={"X-Api-Key": key},
        ), timeout=10).read())
        rfs = _json.loads(_ur.urlopen(_ur.Request(
            f"{base}/rootfolder", headers={"X-Api-Key": key},
        ), timeout=10).read())
        existing = _json.loads(_ur.urlopen(_ur.Request(
            f"{base}/series", headers={"X-Api-Key": key},
        ), timeout=15).read())
    except Exception as exc:
        return {"action": "ensure-sonarr-seed-series",
                "error": f"sonarr precheck failed: {str(exc)[:80]}"}

    qp_id = qps[0]["id"] if qps else 1
    rf_path = rfs[0]["path"] if rfs else "/media/tv"
    existing_tvdbs = {s.get("tvdbId") for s in existing if s.get("tvdbId")}

    added = 0
    skipped = 0
    failed = 0
    for name in series_names[: int(seed.get("max_series", 15))]:
        try:
            q = _up.quote(name)
            hits = _json.loads(_ur.urlopen(_ur.Request(
                f"{base}/series/lookup?term={q}", headers={"X-Api-Key": key},
            ), timeout=10).read())
        except Exception:
            failed += 1
            continue
        if not hits:
            failed += 1
            continue
        s = hits[0]
        tvdb = s.get("tvdbId")
        if tvdb and tvdb in existing_tvdbs:
            skipped += 1
            continue
        body = {
            "title": s["title"],
            "qualityProfileId": qp_id,
            "titleSlug": s.get("titleSlug"),
            "images": s.get("images", []),
            "seasons": s.get("seasons", []),
            "tvdbId": tvdb,
            "rootFolderPath": rf_path,
            "monitored": True,
            "seasonFolder": season_folder,
            "addOptions": {
                "searchForMissingEpisodes": False,  # avoid 429 burst
                "monitor": monitor,
            },
        }
        try:
            _ur.urlopen(_ur.Request(
                f"{base}/series", data=_json.dumps(body).encode(),
                method="POST", headers=H,
            ), timeout=15)
            added += 1
        except Exception:
            failed += 1
    return {
        "action": "ensure-sonarr-seed-series",
        "added": added, "skipped_existing": skipped, "failed": failed,
        "total": len(series_names),
    }


def ensure_jellyseerr_oidc(ctx: JobContext) -> dict:
    """Wire Authelia SSO into Jellyseerr's settings.json.

    Authelia's identity_providers.oidc.clients already has a
    ``jellyseerr`` entry (generated from contracts/auth/oidc_clients.yaml
    by the authelia-config-generator). What's missing is the
    DOWNSTREAM half — Jellyseerr needs ``oidcLogin: true`` and an
    ``oidc`` provider block in its settings.json so the login page
    actually renders the "Sign in with Authelia" button.

    The preview-OIDC Jellyseerr build (fallenbagel/jellyseerr:preview-OIDC)
    consumes these fields. Idempotent — only writes if the existing
    config differs. Restarts Jellyseerr afterward so the change takes
    effect (settings.json is read at boot, not live).
    """
    import json as _json
    from pathlib import Path as _Path
    settings_path = _Path(ctx.config_root) / "jellyseerr" / "settings.json"
    if not settings_path.is_file():
        return {"action": "ensure-jellyseerr-oidc",
                "skipped": f"settings.json not found at {settings_path}"}

    # Resolve the issuer URL from the merged routing config (profile
    # YAML + dashboard runtime overrides) so dashboard-edited
    # hostnames take effect. On K8s the profile ConfigMap is often
    # absent and the operator's real routing lives in
    # ``${CONFIG_ROOT}/.controller/routing-overrides.yaml``;
    # ``get_routing()`` merges both. Authelia's discovery doc lives at
    # /.well-known/openid-configuration on the main hostname.
    try:
        from media_stack.api.services.config import get_routing as _get_routing
        routing = dict(_get_routing() or {})
    except Exception:
        routing = (ctx.profile or {}).get("routing") or {}
    base_domain = str(routing.get("base_domain") or "local").strip()
    sub = str(routing.get("stack_subdomain") or "media-stack").strip()
    issuer = f"https://authelia.{sub}.{base_domain}"

    # The preview-OIDC build's settings schema (PR #1505) uses
    # ``main.openIdProviders`` as an ARRAY of provider entries.
    # The login page renders one button per entry. The earlier
    # single-object ``main.oidc`` shape that worked on older forks
    # is invisible to this build — settings.json validates fine,
    # but ``GET /api/v1/settings/public`` returns
    # ``openIdProviders: []`` and no button shows.
    desired_provider = {
        "slug": "authelia",                 # callback at /api/v1/auth/oidc/authelia/callback
        "name": "Authelia",                 # button text
        "issuerUrl": issuer,
        "clientId": "jellyseerr",
        "clientSecret": "jellyseerr-oidc-secret",
        "scopes": "openid email profile groups",
        "newUserLogin": True,               # auto-provision local user on first login
        "requiredClaims": "",
    }

    # The persisted shape (from server/lib/settings/index.ts:731):
    #   main.oidcLogin: true                 (master toggle)
    #   oidc.providers: [{ slug, name, … }]  (TOP-LEVEL oidc block)
    # The authenticated /api/v1/settings/main endpoint flattens this
    # into ``openIdProviders`` for the UI form, which made earlier
    # debugging misleading — the persistence shape is what matters.
    # applicationUrl + trustProxy are CRITICAL for redirect_uri to
    # match what Authelia registered. Without applicationUrl,
    # Jellyseerr derives the redirect_uri from the raw request Host
    # header (no scheme — defaults to http), producing
    # ``http://apps.media-stack.local/login?...`` which Authelia
    # rejects (its registered URIs are all HTTPS). With trustProxy
    # set, Jellyseerr also honors X-Forwarded-Proto from Envoy so
    # the scheme is correct on subsequent requests.
    gateway_host = str(routing.get("gateway_host") or "").strip() \
        or f"apps.{sub}.{base_domain}"
    application_url = f"https://{gateway_host}/app/jellyseerr"

    settings = _json.loads(settings_path.read_text(encoding="utf-8"))
    main = settings.setdefault("main", {})
    oidc_block = settings.setdefault("oidc", {})
    # PR #1505's migration 0005 moved trustProxy from main.trustProxy
    # to network.trustProxy. Writing it under main.* gets silently
    # stripped on boot — ``req.protocol`` stays http and Jellyseerr
    # builds redirect_uri as ``http://apps.media-stack.local/login?…``
    # which Authelia rejects because its registered URIs are all
    # HTTPS. This was the last mile that kept breaking every OIDC
    # fix round-trip in this session. (v1.0.146 real root cause.)
    network_block = settings.setdefault("network", {})
    changed = False
    if main.get("oidcLogin") is not True:
        main["oidcLogin"] = True
        changed = True
    if main.get("applicationUrl") != application_url:
        main["applicationUrl"] = application_url
        changed = True
    if network_block.get("trustProxy") is not True:
        network_block["trustProxy"] = True
        changed = True
    # Strip the wrong-location variant if a previous run put it
    # under main; the migration removes it on boot, but leaving it
    # in the file causes confusing diff-noise.
    if "trustProxy" in main:
        del main["trustProxy"]
        changed = True
    desired_providers = [desired_provider]
    if oidc_block.get("providers") != desired_providers:
        oidc_block["providers"] = desired_providers
        changed = True
    # Strip leftover wrong-schema fields from earlier debug rounds.
    for stale_key in ("oidc", "openIdProviders"):
        if stale_key in main:
            del main[stale_key]
            changed = True

    if not changed:
        return {"action": "ensure-jellyseerr-oidc",
                "skipped": "oidc config already in sync"}

    settings_path.write_text(_json.dumps(settings, indent=2), encoding="utf-8")

    # Restart Jellyseerr so it loads the new config. Best-effort —
    # docker SDK works in compose, k8s pod-delete works on cluster.
    restarted = False
    try:
        import docker as _docker
        _docker.from_env().containers.get("jellyseerr").restart(timeout=15)
        restarted = True
    except Exception:
        try:
            import os as _os
            from kubernetes import client as _k8s, config as _kc
            try:
                _kc.load_incluster_config()
            except Exception:
                _kc.load_kube_config()
            ns = _os.environ.get("K8S_NAMESPACE", "media-stack")
            v1 = _k8s.CoreV1Api()
            for pod in v1.list_namespaced_pod(ns, label_selector="app=jellyseerr").items:
                v1.delete_namespaced_pod(name=pod.metadata.name, namespace=ns)
            restarted = True
        except Exception as exc:
            # Pod-restart is best-effort; Jellyseerr will pick up the
            # new OIDC config on its next natural restart anyway. Don't
            # fail the whole ensure-jellyseerr-oidc job if the k8s
            # client isn't available (compose path) or RBAC is denied.
            log_swallowed(exc)

    return {
        "action": "ensure-jellyseerr-oidc",
        "issuer": issuer,
        "client_id": "jellyseerr",
        "settings_written": True,
        "restarted": restarted,
    }


def ensure_arr_jellyfin_notifier(ctx: JobContext) -> dict:
    """Add a Jellyfin (MediaBrowser) notifier to each *arr that
    supports it (Sonarr/Radarr/Lidarr — Readarr's notification
    schema doesn't include MediaBrowser yet).

    Why: replaces the controller's ``/webhooks/arr`` →
    ``/Library/Refresh`` round-trip with a direct *arr → Jellyfin
    library-update call. Per-item refresh (only the changed series
    /movie's library) instead of a full scan, no controller
    bottleneck, fires synchronously inside the *arr's import
    pipeline. The webhook stays as a fallback.

    Idempotent: GET /api/vN/notification, skip if a MediaBrowser
    notifier already exists by name (``media-stack-jellyfin``).
    """
    import json as _json
    import urllib.request as _ur
    import urllib.error as _ue
    keys = _discover_api_keys()
    jf_key = keys.get("jellyfin", "")
    if not jf_key:
        return {"action": "ensure-arr-jellyfin-notifier",
                "skipped": "no jellyfin key"}

    notifier_name = "media-stack-jellyfin"
    # Each *arr has a DIFFERENT set of event flag names. Sending an
    # unknown flag is silently ignored, so the union of all names
    # would leave each app missing its own critical events. Map per
    # app explicitly. The semantic intent is identical — "fire on
    # any change to a file on disk."
    common_off = {
        "onGrab": False,
        "onHealthIssue": False,
        "onHealthRestored": False,
        "onApplicationUpdate": False,
        "onManualInteractionRequired": False,
    }
    arr_event_maps = {
        "sonarr": {
            **common_off,
            "onDownload": True,                       # episode imported
            "onUpgrade": True,                        # episode upgraded
            "onImportComplete": True,                 # batch import
            "onRename": True,                         # folder rename
            "onSeriesAdd": False,                     # no file yet
            "onSeriesDelete": True,                   # whole series gone
            "onEpisodeFileDelete": True,              # one file gone
            "onEpisodeFileDeleteForUpgrade": False,   # onUpgrade covers it
        },
        "radarr": {
            **common_off,
            "onDownload": True,                       # movie imported
            "onUpgrade": True,                        # movie upgraded
            "onRename": True,                         # folder rename
            "onMovieAdded": False,                    # no file yet
            "onMovieDelete": True,                    # whole movie gone
            "onMovieFileDelete": True,                # one file gone
            "onMovieFileDeleteForUpgrade": False,     # onUpgrade covers it
        },
        "lidarr": {
            **common_off,
            "onReleaseImport": True,                  # = onDownload (album)
            "onUpgrade": True,
            "onRename": True,                         # artist folder rename
            "onTrackRetag": True,                     # file metadata rewrite
            "onArtistAdd": False,                     # no file yet
            "onArtistDelete": True,
            "onAlbumDelete": True,
            "onDownloadFailure": False,
            "onImportFailure": False,
        },
    }
    arr_specs = [("sonarr", "v3"), ("radarr", "v3"), ("lidarr", "v1")]
    summary: dict[str, str] = {}
    for app, ver in arr_specs:
        url = ctx.service_url(app)
        key = ctx.api_key(app)
        if not url or not key:
            summary[app] = "skipped (no url/key)"
            continue
        # *arrs serve at ``/app/<app>/`` URL base (set by preflight).
        # Hitting the bare ``/api/...`` returns a 307 redirect, and
        # urllib drops the POST body on 307 — request lands with no
        # payload and the *arr rejects it. Always go through the
        # prefixed path.
        base = f"{url.rstrip('/')}/app/{app}/api/{ver}/notification"
        headers = {"X-Api-Key": key, "Content-Type": "application/json"}

        try:
            req = _ur.Request(base, headers={"X-Api-Key": key})
            with _ur.urlopen(req, timeout=10) as r:
                existing = _json.loads(r.read())
        except Exception as exc:
            summary[app] = f"list error: {str(exc)[:60]}"
            continue
        if any(
            isinstance(n, dict) and n.get("name") == notifier_name
            for n in (existing or [])
        ):
            summary[app] = "already configured"
            continue

        payload = {
            "name": notifier_name,
            "implementation": "MediaBrowser",
            "configContract": "MediaBrowserSettings",
            # Per-app event flags from arr_event_maps above. Each
            # *arr only persists fields it knows; sending the union
            # would silently drop the events that have different
            # names on different *arrs (e.g. Lidarr's
            # ``onReleaseImport`` vs Sonarr's ``onDownload``).
            **arr_event_maps[app],
            # ``updateLibrary=true`` (field below) tells Jellyfin to
            # scan the affected path; ``notify=false`` skips the
            # in-app banner so users don't see a popup per import.
            "fields": [
                {"name": "host",          "value": "jellyfin"},
                {"name": "port",          "value": 8096},
                {"name": "useSsl",        "value": False},
                {"name": "urlBase",       "value": ""},
                {"name": "apiKey",        "value": jf_key},
                {"name": "notify",        "value": False},
                {"name": "updateLibrary", "value": True},
            ],
        }
        try:
            req = _ur.Request(
                base, data=_json.dumps(payload).encode(),
                method="POST", headers=headers,
            )
            with _ur.urlopen(req, timeout=15) as r:
                summary[app] = f"created (HTTP {r.status})"
        except _ue.HTTPError as exc:
            summary[app] = f"HTTP {exc.code}: {exc.read()[:80].decode(errors='replace')}"
        except Exception as exc:
            summary[app] = str(exc)[:80]
    return {"action": "ensure-arr-jellyfin-notifier", "summary": summary}


def _qbit_completed_torrents(ctx: JobContext) -> list[dict]:
    """Return qBit torrents at progress=1.0, with the fields needed
    for manualimport routing: name, content_path, category."""
    import http.cookiejar as _cj
    import json as _json
    import urllib.parse as _up
    import urllib.request as _ur
    qb_user = _QBIT_DEFAULT_USERNAME
    qb_pass = _QBIT_DEFAULT_PASSWORD
    base = (ctx.service_url("qbittorrent") or service_internal_url("qbittorrent")).rstrip("/")
    cookies = _cj.CookieJar()
    opener = _ur.build_opener(_ur.HTTPCookieProcessor(cookies))
    try:
        opener.open(_ur.Request(
            f"{base}/api/v2/auth/login",
            data=_up.urlencode({"username": qb_user, "password": qb_pass}).encode(),
        ), timeout=5)
        with opener.open(f"{base}/api/v2/torrents/info", timeout=10) as r:
            torrents = _json.loads(r.read())
    except Exception:
        return []
    return [t for t in torrents if t.get("progress", 0) >= 1.0]


def recover_stuck_imports(ctx: JobContext) -> dict:
    """Find files that qBit finished downloading but the *arr never
    imported, then force-import them via /api/v3/manualimport.

    Why this exists (v1.0.150 — "Shelter incident"): the *arr's
    queue cache can lag qBit by several minutes. Radarr's queue
    sees ``sizeleft: 80MB`` while qBit reports the torrent at
    100% / stalledUP. Auto-scan (DownloadedMoviesScan) skips files
    Radarr's queue thinks are still downloading, so the file
    sits in /data/torrents/completed/ forever.

    ManualImport bypasses the queue check, force-imports the file,
    then we DELETE the stale queue entry so the next recovery tick
    doesn't redo the same work. The torrent stays in qBit (seeding)
    — media-hygiene handles eventual cleanup by age/ratio, same as
    a normally-imported torrent. ``RECOVER_STUCK_REMOVE_FROM_QBIT=1``
    overrides for low-disk hosts that want aggressive cleanup.
    """
    import json as _json
    import urllib.parse as _up
    import urllib.request as _ur
    import urllib.error as _ue
    remove_from_qbit = os.environ.get(
        "RECOVER_STUCK_REMOVE_FROM_QBIT", "0",
    ).strip() == "1"
    arr_specs = [
        # (app, api_ver, queue_movie_field, qbit_category)
        ("sonarr",  "v3", "seriesId", "tv"),
        ("radarr",  "v3", "movieId",  "movies"),
        ("lidarr",  "v1", "albumId",  "music"),
    ]
    # qBit is the source of truth for "what's actually done".
    # In-flight torrents (progress<1.0) have no data on disk yet,
    # so it's wrong to try to import them — only consider
    # progress=1.0. Group by category so we know which *arr to
    # ask about each torrent.
    qbit_done = _qbit_completed_torrents(ctx)
    by_cat: dict[str, list[dict]] = {}
    for t in qbit_done:
        by_cat.setdefault(str(t.get("category") or ""), []).append(t)
    summary: dict[str, dict] = {}
    for app, ver, id_field, _cat in arr_specs:
        url = ctx.service_url(app)
        key = ctx.api_key(app)
        if not url or not key:
            summary[app] = {"skipped": "no url/key"}
            continue
        api_base = f"{url.rstrip('/')}/app/{app}/api/{ver}"
        H = {"X-Api-Key": key, "Content-Type": "application/json"}

        # 1. Get this *arr's current queue (so we can DELETE the
        #    stuck entry after a successful import) — keyed by
        #    qBit downloadId (hash) so we can match by torrent.
        try:
            req = _ur.Request(
                f"{api_base}/queue?pageSize=200",
                headers={"X-Api-Key": key},
            )
            with _ur.urlopen(req, timeout=15) as r:
                queue_records = _json.loads(r.read()).get("records", [])
        except Exception:
            queue_records = []
        queue_by_hash = {
            (q.get("downloadId") or "").upper(): q for q in queue_records
        }

        # 2. For every qBit torrent in THIS *arr's category that's
        #    100% complete, ask the *arr to manualimport its
        #    content_path. The *arr decides if there's a match.
        candidates = by_cat.get(_cat, [])
        recovered = 0
        skipped = 0
        errors = 0
        match_id_field = (
            "movie" if app == "radarr"
            else "series" if app == "sonarr"
            else "album"
        )
        for t in candidates:
            content_path = t.get("content_path") or ""
            tor_hash = (t.get("hash") or "").upper()
            if not content_path:
                skipped += 1
                continue
            try:
                probe_url = (
                    f"{api_base}/manualimport"
                    f"?folder={_up.quote(content_path)}&filterExistingFiles=true"
                )
                req = _ur.Request(probe_url, headers={"X-Api-Key": key})
                with _ur.urlopen(req, timeout=20) as r:
                    items = _json.loads(r.read())
            except Exception:
                errors += 1
                continue
            # Need: at least one file matched to a known *arr item
            # with no rejections. ``filterExistingFiles=true`` makes
            # this a no-op when /media/<title>/ already has the file.
            usable = [
                i for i in items
                if not i.get("rejections")
                and i.get("path")
                and (i.get(match_id_field) or {}).get("id")
            ]
            if not usable:
                skipped += 1
                continue
            files_payload = []
            for i in usable:
                matched_id = (i.get(match_id_field) or {}).get("id")
                entry = {
                    "path": i["path"],
                    id_field: int(matched_id),
                    "quality": i.get("quality") or {},
                }
                if app == "sonarr":
                    eps = i.get("episodes") or []
                    if eps:
                        entry["episodeIds"] = [
                            e.get("id") for e in eps if e.get("id")
                        ]
                files_payload.append(entry)
            cmd_body = {
                "name": "ManualImport", "files": files_payload,
                "importMode": "auto",
            }
            try:
                req = _ur.Request(
                    f"{api_base}/command",
                    data=_json.dumps(cmd_body).encode(),
                    method="POST", headers=H,
                )
                _ur.urlopen(req, timeout=15)
                recovered += 1
            except Exception:
                errors += 1
                continue

            # Clear matching queue entry by torrent hash so the
            # *arr's "still downloading" cache doesn't re-flag
            # this on the next tick. removeFromClient governed by
            # env (default off — keep seeding, media-hygiene cleans
            # up eventually).
            stale_q = queue_by_hash.get(tor_hash)
            if stale_q and stale_q.get("id"):
                try:
                    rm_param = "true" if remove_from_qbit else "false"
                    del_url = (
                        f"{api_base}/queue/{stale_q['id']}"
                        f"?removeFromClient={rm_param}&blocklist=false"
                    )
                    _ur.urlopen(_ur.Request(
                        del_url, method="DELETE", headers={"X-Api-Key": key},
                    ), timeout=10)
                except Exception as exc:
                    # Per-entry delete failure — next run of recover-
                    # stuck-imports will try again. Don't abort the
                    # batch because one queue entry refuses to delete
                    # (usually the queue-API race between list + delete).
                    log_swallowed(exc)

        summary[app] = {
            "qbit_completed_in_category": len(candidates),
            "imported": recovered,
            "skipped": skipped,
            "errors": errors,
        }
    return {
        "action": "recover-stuck-imports",
        "remove_from_qbit": remove_from_qbit,
        "summary": summary,
    }


def scan_completed_downloads(ctx: JobContext) -> dict:
    """Tell each *arr to scan its completed-downloads path and import
    anything it recognizes.

    Two paths into qBit produce content:
      1. *arr drives qBit (Sonarr/Radarr add the torrent, qBit
         downloads, *arr's webhook fires on completion → import)
      2. The user adds a torrent to qBit directly (no *arr knows)

    Path 2 leaves files in ``/data/torrents/completed/...`` that
    nothing imports — they sit there forever, never reaching the
    Jellyfin library.

    Each *arr has a "scan downloaded" command (Sonarr's
    ``DownloadedEpisodesScan``, Radarr's ``DownloadedMoviesScan``,
    etc.) that walks its configured download path and imports any
    file it can identify by metadata. Firing these on a 15m
    schedule means user-added qBit content reaches Jellyfin within
    one tick — no manual intervention.
    """
    import json as _json
    import urllib.request as _ur
    import urllib.error as _ue
    # ``DownloadedXScan`` command name + completed path per *arr.
    # Path defaults match qBit's category save_paths configured
    # in download_clients.yaml + bin/init-permissions.
    arr_specs = [
        ("sonarr",  "v3", "DownloadedEpisodesScan", "/data/torrents/completed/tv"),
        ("radarr",  "v3", "DownloadedMoviesScan",   "/data/torrents/completed/movies"),
        ("lidarr",  "v1", "DownloadedAlbumsScan",   "/data/torrents/completed/music"),
        ("readarr", "v1", "DownloadedBooksScan",    "/data/torrents/completed/books"),
    ]
    fired: dict[str, str] = {}
    for app, ver, cmd, path in arr_specs:
        url = ctx.service_url(app)
        key = ctx.api_key(app)
        if not url or not key:
            fired[app] = "skipped (no url/key)"
            continue
        endpoint = f"{url.rstrip('/')}/api/{ver}/command"
        body = _json.dumps({"name": cmd, "path": path}).encode()
        req = _ur.Request(endpoint, data=body, method="POST", headers={
            "X-Api-Key": key, "Content-Type": "application/json",
        })
        try:
            with _ur.urlopen(req, timeout=10) as resp:
                fired[app] = f"queued ({resp.status})"
        except _ue.HTTPError as exc:
            fired[app] = f"HTTP {exc.code}"
        except Exception as exc:
            fired[app] = str(exc)[:80]
    return {"action": "scan-completed-downloads", "summary": fired}


def apply_arr_runtime_defaults(ctx: JobContext) -> dict:
    """Patch each *arr's runtime quality settings to release-friendly
    defaults. Without this, the *arr family ships defaults that
    reject most real-world torrent formats — Radarr's
    ``language=Original`` rejects English releases for movies TMDB
    mislabels as foreign-language; Lidarr's MP3-tuned per-quality
    size caps reject any FLAC album over ~300 MB; Readarr's eBook
    profile excludes loosely-tagged formats. This is the smallest
    set of patches that makes a fresh install actually grab content."""
    from media_stack.services.apps.servarr.arr_runtime_defaults import (
        apply_arr_runtime_defaults as _apply,
    )
    cfg = ctx.cfg
    # Build arr_apps from the registry (the cfg.arr_apps key is
    # legacy / never populated on contract-driven deploys; the fall-
    # back of `[]` made apply_arr_runtime_defaults a silent NOOP →
    # delay profile stayed at preferredProtocol=usenet even when
    # SAB was off, and qBit grabs hung in "delay" status forever.
    # v1.0.135 — derive arr_apps from ctx.service_url like every
    # other adapter does.)
    arr_apps = cfg.get("arr_apps") or []
    if not isinstance(arr_apps, list):
        arr_apps = []
    app_keys = {
        name: ctx.api_key(name)
        for name in ("sonarr", "radarr", "lidarr", "readarr")
        if ctx.api_key(name)
    }
    if not arr_apps:
        for name in ("sonarr", "radarr", "lidarr", "readarr"):
            url = ctx.service_url(name)
            if not url or not app_keys.get(name):
                continue
            arr_apps.append({
                "name": name.capitalize(),
                "implementation": name.capitalize(),
                "url": url,
            })
        # The keys downstream are looked up by capitalized name.
        app_keys = {n.capitalize(): k for n, k in app_keys.items()}
    # Lazy http_request: re-use the same shape the rest of the
    # *arr ops use — small synchronous urllib wrapper.
    import json as _json
    import urllib.request as _ur
    import urllib.error as _ue

    _http_request = _make_servarr_http_request()

    def _log(msg: str) -> None:
        from media_stack.services.jobs.controller_runner import (
            runtime_platform,
        )
        runtime_platform.log(msg)

    # Usenet-enabled gate reads ``download_clients.sabnzbd.configure_arr_clients``
    # from the cfg. When false (the default as of v1.0.111), each
    # *arr's SAB download client gets enable=false and the delay
    # profile flips to preferredProtocol=torrent so qBittorrent
    # grabs fire immediately. When the operator flips this back to
    # true via the dashboard toggle + reconcile, the *arrs are
    # reconciled back to usenet-preferred.
    dcs_cfg = cfg.get("download_clients") or {}
    sab_cfg = dcs_cfg.get("sabnzbd") if isinstance(dcs_cfg, dict) else {}
    usenet_enabled = bool(
        isinstance(sab_cfg, dict) and sab_cfg.get("configure_arr_clients", False)
    )
    summary = _apply(
        arr_apps=arr_apps,
        app_keys=app_keys,
        service_url=ctx.service_url,
        http_request=_http_request,
        log=_log,
        usenet_enabled=usenet_enabled,
    )
    return {
        "action": "apply-arr-runtime-defaults",
        "updated": summary,
        "usenet_enabled": usenet_enabled,
    }


def post_setup(ctx: JobContext) -> dict:
    """Deferred post-bootstrap: media-server tuning, hygiene, app
    restarts."""
    from media_stack.services.jobs.action_handlers import action_post_setup
    from media_stack.services.jobs.controller_runner import _build_runner
    from media_stack.services.jobs.controller_handlers import (
        _run_post_bootstrap,
    )
    action_post_setup(
        _default_args(ctx), _stub_state(),
        _build_runner, _run_post_bootstrap,
    )
    return {"action": "post-setup"}


def discover_api_keys(ctx: JobContext) -> dict:
    """Run every container preflight handler (per-app probe +
    API-key discovery + key persistence to env/secret).

    Preflights are idempotent — they probe each app, harvest its
    API key from disk or HTTP, and update the env. Running them
    every bootstrap (and every reconcile) catches drift introduced
    by a manual key rotation in a service's UI without making the
    operator remember to "re-bootstrap from scratch".

    Failure modes hardened in v1.0.181:

    - A single preflight handler raising no longer aborts the
      whole job. ``_run_preflights`` already catches per-handler
      errors, but transient failures (Jellyfin not yet bootstrapped
      on a fresh stack) used to surface here as a top-level
      ``status: error`` entry in ``/api/jobs.history``. We now
      treat per-service unavailability as a per-service skip and
      always return a structured summary.
    - Even when no preflight populated ``state.preflight_results``
      (the ``_stub_state`` short-circuit, or every handler erroring)
      we fall back to reading every service's on-disk
      ``config.xml`` directly. This is the canonical source of
      truth — if a key exists on the PVC, the job persists it,
      regardless of whether the live service was reachable.
    - Secret-write failures (RBAC missing ``patch`` on secrets,
      compose deploys with no K8s at all) are reported in the
      result dict instead of raising — they don't invalidate the
      keys we just resolved.

    This used to live inline in ``action_bootstrap`` and was
    skippable via ``BOOTSTRAP_RUN_PREFLIGHTS=0`` (used by the old
    ``reconcile`` action). Folding it into the job framework
    removes the env-var dispatch hack — every code path that wants
    a fresh tree walk now goes through the same ``run_job("bootstrap")``.
    """
    from media_stack.services.jobs.controller_handlers import _run_preflights
    args = _default_args(ctx)
    state = _stub_state()
    skipped: list[str] = []
    try:
        _run_preflights(state, args)
    except Exception as exc:
        # Belt-and-suspenders: per-handler errors are already
        # swallowed inside ``_exec_spec``, so anything that bubbles
        # up here is something structural (e.g. config.json malformed,
        # YAML parse error). Log + carry on — the on-disk fallback
        # below still resolves keys for services whose files are
        # readable.
        skipped.append(f"_run_preflights: {str(exc)[:80]}")

    discovered, file_skipped = _harvest_keys_from_disk(args.config_root)
    skipped.extend(file_skipped)

    # Push everything we learned into the live process env so
    # ``read_service_api_key`` callers in the same process see the
    # update without waiting for the cache TTL.
    import os as _os
    for env_var, value in discovered.items():
        if value and not _os.environ.get(env_var):
            _os.environ[env_var] = value

    persist_result = _persist_preflight_keys_to_secret_safe(
        state, discovered,
    )

    # Bust the runtime_keys cache so subsequent /api/libraries calls
    # in the same process pick up the freshly-written values.
    try:
        from media_stack.api.services.runtime_keys import invalidate_cache
        invalidate_cache()
    except Exception as exc:
        log_swallowed(exc)

    return {
        "action": "discover-api-keys",
        "discovered": sorted(discovered.keys()),
        "skipped": skipped,
        "persist": persist_result,
    }


def _harvest_keys_from_disk(config_root: str) -> tuple[dict[str, str], list[str]]:
    """Walk every service's ``api_key_config`` file under ``config_root``.

    Returns ``(env_var_to_value, per_service_skip_reasons)``. Per-service
    failures are recorded as ``"<svc>: <reason>"`` strings instead of
    raising, so the discover-api-keys job can treat partial coverage as
    success. ``previous_value`` from ``os.environ`` is preserved when a
    file is unreadable in *this* run — we never overwrite a known-good
    key with empty just because the PVC was momentarily unmounted.
    """
    from media_stack.api.services.registry import (
        SERVICES,
        read_api_key_from_file,
    )
    import os as _os
    discovered: dict[str, str] = {}
    skipped: list[str] = []
    for svc in SERVICES:
        if not svc.api_key_env:
            continue
        try:
            key = read_api_key_from_file(svc.id, config_root) or ""
        except Exception as exc:
            skipped.append(f"{svc.id}: parse-failed {str(exc)[:60]}")
            key = ""
        if not key and svc.id == "jellyfin":
            # Jellyfin is special — its key lives in SQLite, not a
            # config file. On a fresh stack the DB doesn't exist yet,
            # which we report as an intentional skip rather than an
            # error; the next run after bootstrap will pick it up.
            try:
                from media_stack.services.apps.jellyfin.api_key_db import (
                    read_jellyfin_api_key_from_db,
                )
                from pathlib import Path as _Path
                token, _ = read_jellyfin_api_key_from_db(
                    str(config_root),
                    {
                        "api_key_db_path": "jellyfin/data/jellyfin.db",
                        "api_key_name_preference": [
                            "Jellyfin", "Jellyseerr", "media-stack-controller",
                        ],
                    },
                    coerce_list=lambda v: list(v) if isinstance(v, (list, tuple)) else [v],
                    resolve_path=lambda root, rel: _Path(root) / rel,
                )
                if token:
                    key = str(token).strip()
                else:
                    skipped.append("jellyfin: not bootstrapped")
            except Exception as exc:
                skipped.append(f"jellyfin: db-unavailable {str(exc)[:60]}")
        if key:
            discovered[svc.api_key_env] = key
        else:
            # Preserve the previous value from env — never overwrite
            # a known-good key with empty just because the file isn't
            # readable in this run.
            prev = (_os.environ.get(svc.api_key_env) or "").strip()
            if prev:
                discovered[svc.api_key_env] = prev
                skipped.append(f"{svc.id}: file-unreadable, kept env value")
            else:
                if svc.id != "jellyfin":  # already recorded above
                    skipped.append(f"{svc.id}: no key on disk")
    return discovered, skipped


def _persist_preflight_keys_to_secret_safe(
    state: object, discovered: dict[str, str],
) -> dict:
    """Wrap ``_persist_preflight_keys_to_secret`` and report instead of raise.

    Returns ``{"status": "...", "written": [...], "reason": "..."}``.

    - K8s deploys: patch the ``media-stack-secrets`` Secret. RBAC errors
      (403 on ``patch`` of secrets) come back as
      ``status: rbac-denied`` so the dashboard can prompt the operator
      to re-apply ``k8s/base/controller/controller.yaml`` instead of silently doing
      nothing.
    - Compose deploys: no K8s namespace → ``status: skipped-no-k8s``.
      Discovered values still live in ``os.environ`` for the running
      controller, and the bootstrap's
      ``.controller/_runtime_env_overrides.yaml`` mechanism handles
      cross-process propagation.
    """
    import os as _os
    namespace = _os.environ.get("K8S_NAMESPACE", "")
    if not namespace:
        return {"status": "skipped-no-k8s", "written": []}
    if not discovered:
        return {"status": "skipped-empty", "written": []}
    secret_name = _os.environ.get("K8S_SECRET_NAME", "media-stack-secrets")
    try:
        from kubernetes import client, config  # type: ignore[import-untyped]
        try:
            config.load_incluster_config()
        except Exception:
            config.load_kube_config()
        v1 = client.CoreV1Api()
        import base64 as _b64
        patch_body = {
            "data": {
                k: _b64.b64encode(v.encode()).decode()
                for k, v in discovered.items()
            }
        }
        v1.patch_namespaced_secret(
            name=secret_name, namespace=namespace, body=patch_body,
        )
        return {
            "status": "ok",
            "written": sorted(discovered.keys()),
            "secret": f"{namespace}/{secret_name}",
        }
    except Exception as exc:
        message = str(exc)
        # Surface RBAC denials specifically — the operator can fix this
        # by re-applying ``k8s/base/controller/controller.yaml``; an opaque "Forbidden"
        # in the job log isn't actionable.
        status = "rbac-denied" if "403" in message or "Forbidden" in message else "error"
        return {
            "status": status,
            "written": [],
            "reason": message[:200],
        }


def run_legacy_pipeline(ctx: JobContext) -> dict:
    """Run the legacy adapter-hooks pipeline (``runner.run``).

    The legacy runner is what installs/configures every app via
    the older adapter_hooks contract. The newer per-service jobs
    (configure-libraries, configure-indexers, etc.) cover the same
    surface but the legacy runner is still load-bearing: removing
    it would silently drop work for any service that hasn't been
    fully migrated to a contract job.

    This used to be the second half of ``action_bootstrap``'s
    inline orchestration. As a contract job it slots into the
    pre_bootstrap phase right after ``discover-api-keys``, so per-
    app phase jobs find runtime state populated."""
    from media_stack.services.jobs.controller_runner import _build_runner
    runner, runtime_state = _build_runner(_default_args(ctx))
    runner.run(runtime_state)
    return {"action": "run-legacy-pipeline"}
