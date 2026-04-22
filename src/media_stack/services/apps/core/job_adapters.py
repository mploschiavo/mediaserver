"""JobContext adapters for the six core actions migrated from the
``_CORE_ACTIONS`` table into the job framework.

Each adapter is a thin wrapper that:

1. Constructs the legacy-style ``args``/``state``/``build_runner``
   collaborators that the original ``action_*`` handler expects.
2. Calls into the existing handler in
   ``media_stack.cli.commands.action_handlers``.
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

from media_stack.cli.commands.job_framework import JobContext


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
    from media_stack.cli.commands.action_handlers import action_envoy_config
    action_envoy_config(_default_args(ctx))
    return {"action": "envoy-config"}


def validate_credentials(ctx: JobContext) -> dict:
    """Probe service admin credentials and auto-sync passwords for
    services that fail."""
    from media_stack.cli.commands.action_handlers import (
        action_validate_credentials,
    )
    action_validate_credentials()
    return {"action": "validate-credentials"}


def restart_apps(ctx: JobContext) -> dict:
    """Restart all apps to pick up config changes."""
    from media_stack.cli.commands.action_handlers import action_restart_apps
    from media_stack.cli.commands.controller_handlers import (
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
        from media_stack.cli.commands.action_handlers import (
            action_discover_indexers,
        )
        from media_stack.cli.commands.controller_runner import _build_runner
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
        from media_stack.cli.commands.controller_runner import (
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


def push_indexers(ctx: JobContext) -> dict:
    """Trigger indexer-manager ApplicationIndexerSync."""
    from media_stack.cli.commands.action_handlers import action_push_indexers
    from media_stack.cli.commands.controller_runner import _build_runner
    action_push_indexers(_default_args(ctx), _build_runner)
    return {"action": "push-indexers"}


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
    arr_apps = cfg.get("arr_apps") or []
    if not isinstance(arr_apps, list):
        arr_apps = []
    app_keys = {
        name: ctx.api_key(name)
        for name in ("sonarr", "radarr", "lidarr", "readarr")
        if ctx.api_key(name)
    }
    # Lazy http_request: re-use the same shape the rest of the
    # *arr ops use — small synchronous urllib wrapper.
    import json as _json
    import urllib.request as _ur
    import urllib.error as _ue

    _http_request = _make_servarr_http_request()

    def _log(msg: str) -> None:
        from media_stack.cli.commands.controller_runner import (
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
    from media_stack.cli.commands.action_handlers import action_post_setup
    from media_stack.cli.commands.controller_runner import _build_runner
    from media_stack.cli.commands.controller_handlers import (
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

    This used to live inline in ``action_bootstrap`` and was
    skippable via ``BOOTSTRAP_RUN_PREFLIGHTS=0`` (used by the old
    ``reconcile`` action). Folding it into the job framework
    removes the env-var dispatch hack — every code path that wants
    a fresh tree walk now goes through the same ``run_job("bootstrap")``.
    """
    from media_stack.cli.commands.controller_handlers import _run_preflights
    from media_stack.cli.commands.controller_k8s import (
        _persist_preflight_keys_to_secret,
    )
    args = _default_args(ctx)
    state = _stub_state()
    _run_preflights(state, args)
    _persist_preflight_keys_to_secret(state)
    return {"action": "discover-api-keys"}


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
    from media_stack.cli.commands.controller_runner import _build_runner
    runner, runtime_state = _build_runner(_default_args(ctx))
    runner.run(runtime_state)
    return {"action": "run-legacy-pipeline"}
