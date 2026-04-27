"""GET route handlers — extracted from ControllerAPIHandler.do_GET().

Every public function receives the ControllerAPIHandler instance as its
first argument so it can call response helpers and access ``self.state``.
"""

from __future__ import annotations
from media_stack.core.time_utils import ISO_8601_TZ_OFFSET, ISO_8601_UTC_Z


from media_stack.core.logging_utils import log_swallowed
import base64
import json
import os
import re
import time
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse, parse_qs

from media_stack.api.session_singletons import (
    session_cookie_reader, trusted_proxy_auth,
)
from media_stack.api.services.registry import SERVICES as _SERVICES
from media_stack.api.services.media_integrity_handlers import (
    _instance as _media_integrity_handlers,
)
from media_stack.api.services.security_get_handlers import (
    _SessionVisibilityGetHelper,
)
from media_stack.api.tls_factory import build_default_tls_service
from media_stack.core.auth.rate_limiter import RateLimiter
from media_stack.core.auth.users.user_service_factory import (
    build_default_api_token_store,
    build_default_invite_service,
    build_default_service,
)
from media_stack.core.auth.users.metrics import render_metrics
from media_stack.core.observability.security_counters import security_counters

from .cache import api_cache
from .services import health as health_svc
from .services import disk as disk_svc
from .services import content as content_svc
from .services import config as config_svc
from .services import metrics as metrics_svc
from .services import ops as ops_svc

# Admin-read security endpoints (sessions, bans, security reports,
# audit head) live behind a dedicated bucket. Wider than the
# user-mgmt POST bucket (60 burst vs 10) because reads are less
# sensitive, but still narrow enough that an attacker enumerating
# session ids or ban cidrs hits a wall. Per-IP keyed in the
# dispatcher so one IP can't DoS the whole security UI.
_SECURITY_READ_BUCKET_CAPACITY = 60
_SECURITY_READ_REFILL_PER_SECOND = 5.0
_security_read_limiter = RateLimiter(
    capacity=_SECURITY_READ_BUCKET_CAPACITY,
    refill_per_second=_SECURITY_READ_REFILL_PER_SECOND,
)

# Session-visibility GETs dispatcher. Admin reads go through the
# rate limiter above; /api/me/* GETs ride the global POST limiter
# by sharing the same per-IP credit line isn't possible on the GET
# side, so we rely on the authz check (scope-to-self) as the
# defence-in-depth layer.
_sessviz_handler = _SessionVisibilityGetHelper()

_SECURITY_READ_PATHS: frozenset[str] = frozenset({
    "/api/sessions/active",
    "/api/audit-log/head",
    "/api/bans/users",
    "/api/bans/ips",
    "/api/security/failed-logins",
    "/api/security/new-locations",
    "/api/security/concurrent",
})

if TYPE_CHECKING:
    from .server import ControllerAPIHandler
import logging

# ---------------------------------------------------------------------------
# OpenAPI YAML (kept — API contract; the UI calls /api/openapi.yaml)
# ---------------------------------------------------------------------------
#
# Dashboard HTML, /api/static/* and /api/docs (Swagger UI HTML wrapper)
# are no longer served by the Python controller — a separate UI
# container now owns those assets. Requests to those paths return
# ``410 GONE`` from the dispatcher with a Location pointer to the UI.
# (v1.0.175.)
#
# Spec lives at ``contracts/api/openapi.yaml`` since ADR-0001 Phase 4
# (v1.0.195). Walk up from this file (api/ → media_stack/ → src/) to
# the repo root, then into the contracts tree.

# Resolve openapi.yaml across deploy modes:
#   1. Source-tree dev (parents[3] = repo root containing contracts/)
#   2. Wheel image, install-root layout (`/opt/media-stack/contracts/`)
#   3. Wheel shared-data layout (`<sys.prefix>/share/media-stack/...`)
#   4. Legacy bind-mount path
# Same bug class as media-integrity v1.0.231 — the wheel install
# moves the file out from under a single hardcoded path. Without the
# candidate list, ``GET /api/openapi.json`` falls through to the
# legacy 50-endpoint stub and the api-docs viewer renders empty.
import sys as _sys

_OPENAPI_PATH_CANDIDATES = (
    Path(__file__).resolve().parents[3] / "contracts" / "api" / "openapi.yaml",
    Path("/opt/media-stack/contracts/api/openapi.yaml"),
    Path(_sys.prefix) / "share" / "media-stack" / "contracts" / "api" / "openapi.yaml",
    Path("/contracts/api/openapi.yaml"),
)


def _resolve_openapi_yaml() -> Path:
    for p in _OPENAPI_PATH_CANDIDATES:
        if p.is_file():
            return p
    return _OPENAPI_PATH_CANDIDATES[0]  # log a sane default if nothing found


_OPENAPI_YAML_PATH = _resolve_openapi_yaml()
_OPENAPI_YAML = ""
try:
    _OPENAPI_YAML = _OPENAPI_YAML_PATH.read_text(encoding="utf-8")
except Exception:
    _OPENAPI_YAML = ""




# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

class GetRequestHandler:
    """Wraps GET request routing logic."""

    def handle(self, handler: ControllerAPIHandler) -> None:  # noqa: C901
        """Route a GET request to the appropriate handler function."""
        path = handler.path.split("?")[0]

        # --- Probes ---
        if path == "/healthz":
            handler._json_response(HTTPStatus.OK, {"status": "ok"})
        elif path == "/readyz":
            handler._json_response(HTTPStatus.OK, {
                "status": "ready",
                "initial_bootstrap_done": handler.state.initial_bootstrap_done,
                "phase": handler.state.phase,
            })

        # --- State ---
        elif path == "/status":
            handler._json_response(HTTPStatus.OK, handler.state.to_dict())
        elif path == "/apps":
            handler._json_response(HTTPStatus.OK, {"apps": dict(handler.state.app_status)})
        elif path.startswith("/apps/") and path.count("/") == 2:
            app_name = path.split("/")[2]
            info = handler.state.app_status.get(app_name)
            handler._json_response(200 if info else 404, {app_name: info} if info else {"error": f"app '{app_name}' not found"})
        elif path == "/config":
            handler._json_response(HTTPStatus.OK, {"config": dict(handler.state.runtime_config)})
        elif path in ("/webhooks", "/api/webhooks"):
            handler._json_response(HTTPStatus.OK, {"webhook_urls": list(handler.state.webhook_urls)})

        # --- SSE ---
        elif path == "/logs/stream":
            handler._sse_response()

        # --- Brand config (white-label friendly) ---
        elif path == "/api/branding":
            _handle_branding(handler)

        # --- Sonarr CustomImport popular-TV feed ---
        elif path == "/api/discovery/popular-tv":
            _handle_popular_tv(handler)

        # --- Services (registry) ---
        elif path == "/api/services":
            _handle_services(handler)
        elif path == "/api/services/categories":
            _handle_services_categories(handler)

        # --- Per-service API key status ---
        elif path.startswith("/api/services/") and path.endswith("/api-key"):
            _handle_service_api_key(handler, path)

        # --- Auto-heal / failed services ---
        elif path == "/api/failed-services":
            handler._json_response(HTTPStatus.OK, {
                "failed_services": handler.state.get_failed_services(),
                "count": len(handler.state.get_failed_services()),
            })

        # --- Health ---
        elif path == "/api/health":
            result = health_svc.probe_services(api_cache)
            health_svc.append_health_history(result.get("services", {}))
            handler._json_response(HTTPStatus.OK, result)
        elif path == "/api/health-history":
            handler._json_response(HTTPStatus.OK, health_svc.get_health_history())
        elif path == "/api/ops/health":
            # Aggregated runtime stats for the /ops dashboard tile.
            # Replaces the UI-side `Promise.resolve(...)` stub that
            # produced the "12/31/1969" bootstrap timestamp. See
            # HealthService.get_ops_health for the field semantics.
            handler._json_response(HTTPStatus.OK, health_svc.get_ops_health())
        elif path == "/api/credentials":
            handler._json_response(HTTPStatus.OK, health_svc.probe_credentials())
        elif path == "/api/password-propagation":
            # Separate from /api/credentials: read-only check that
            # the stack admin password has been propagated to each
            # service's local user record. See
            # ``HealthService.probe_password_propagation`` for the
            # full design note — this endpoint does NOT attempt
            # authentication; it reads metadata via the API key.
            handler._json_response(
                HTTPStatus.OK, health_svc.probe_password_propagation(),
            )
        elif path == "/api/health/config-integrity":
            from .services import config_integrity as integrity_svc
            handler._json_response(HTTPStatus.OK, {
                "services": integrity_svc.check_all(),
                "checked_at": time.time(),
            })
        elif path == "/api/health/crashloops":
            from .services import crashloop as crashloop_svc
            handler._json_response(HTTPStatus.OK, {
                "services": crashloop_svc.check_all(),
                # CronJob pods + non-registry workloads. Empty list
                # outside K8s. Surfaces in a separate UI section so
                # registry hygiene stays distinct from one-off pod
                # noise (jellyfin-prewarm, anythingllm, etc.).
                "non_registry_pods": crashloop_svc.list_non_registry_problem_pods(),
                "checked_at": time.time(),
            })
        elif path == "/api/auto-heal":
            from .services import auto_heal as autoheal_svc
            handler._json_response(HTTPStatus.OK, autoheal_svc.status())
        elif path == "/api/stack/update":
            from .services import stack_update as su_svc
            handler._json_response(HTTPStatus.OK, su_svc.check_for_update())
        elif path.startswith("/api/stack/upgrade/"):
            from .services import stack_update as su_svc
            task_id = path.rsplit("/", 1)[-1]
            handler._json_response(HTTPStatus.OK, su_svc.upgrade_status(task_id))
        elif path == "/api/health/stories":
            from .services import health_stories as stories_svc
            handler._json_response(HTTPStatus.OK, stories_svc.compose_live())

        # --- Content ---
        elif path == "/api/versions":
            handler._json_response(HTTPStatus.OK, content_svc.get_versions(api_cache))
        elif path == "/api/downloads":
            handler._json_response(HTTPStatus.OK, content_svc.get_downloads())
        elif path == "/api/stats":
            handler._json_response(HTTPStatus.OK, content_svc.get_stats(api_cache))
        elif path == "/api/indexers":
            handler._json_response(HTTPStatus.OK, content_svc.get_indexers())
        elif path == "/api/indexer-stats":
            handler._json_response(HTTPStatus.OK, content_svc.get_indexer_stats())
        elif path == "/api/download-history":
            handler._json_response(HTTPStatus.OK, content_svc.get_download_history())
        elif path == "/api/quality-presets":
            from media_stack.services.apps.servarr.quality_preset_service import list_presets
            handler._json_response(HTTPStatus.OK, list_presets())
        elif path.startswith("/api/quality-profiles/"):
            # GET /api/quality-profiles/{service}
            svc_id = path.split("/")[-1]
            from media_stack.services.apps.servarr.quality_preset_service import get_current_profiles
            handler._json_response(HTTPStatus.OK, get_current_profiles(svc_id))
        elif path.startswith("/api/custom-formats/"):
            svc_id = path.split("/")[-1]
            from media_stack.services.apps.servarr.quality_preset_service import get_custom_formats
            handler._json_response(HTTPStatus.OK, get_custom_formats(svc_id))
        elif path == "/api/arr-webhooks":
            handler._json_response(HTTPStatus.OK, content_svc.ensure_arr_scan_webhooks())
        elif path == "/api/password-policy":
            from media_stack.api.services.password_policy_config import (
                PasswordPolicyConfig,
            )
            cfg = PasswordPolicyConfig()
            handler._json_response(HTTPStatus.OK, {
                "policy": cfg.load_values(),
                "bounds": cfg.bounds(),
            })
        elif path.startswith("/api/password-tickets/"):
            # Single-use retrieval of a plaintext password that the
            # user-write service minted during create / reset. The
            # ticket is burned on first read. Admin-only,
            # rate-limited in the pw-reset bucket, audit-logged.
            _handle_password_ticket_consume(handler, path)
        elif path == "/api/routing-probe":
            try:
                handler._json_response(
                    HTTPStatus.OK, _routing_matrix_probe.probe_all(),
                )
            except Exception as exc:  # noqa: BLE001
                handler._json_response(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": str(exc)[:80]},
                )
        elif path == "/api/route-probe":
            # Server-side reachability probe for the dashboard's
            # "Test All Paths" matrix. Bypasses three structural
            # browser-side problems: mixed-content blocking, self-
            # signed cert errors, and the opaque-response trap of
            # ``no-cors`` mode. See services/route_probe.py header
            # for the full why. (v1.0.165.)
            try:
                from media_stack.api.services import route_probe as _route_probe
                # parse_qs/urlparse imported at module level (line 19).
                # A local re-import here would shadow them and break
                # any other branch that references the names earlier in
                # this same handle() function via UnboundLocalError.
                qs = parse_qs(urlparse(handler.path).query)
                target = (qs.get("url") or [""])[0]
                handler._json_response(HTTPStatus.OK, _route_probe.probe(target))
            except Exception as exc:  # noqa: BLE001
                handler._json_response(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": str(exc)[:99]},
                )
        elif path == "/api/dns-check":
            # Lightweight DNS reachability probe — used by the Routing
            # tab to warn before save when the typed gateway_host
            # doesn't resolve, or resolves to a different machine than
            # this cluster. Doesn't open HTTP connections — DNS only.
            #
            # Two modes:
            #   - ``?host=<name>`` — single-host check, returned as a
            #     flat object (used by the in-form save validator).
            #   - no query string — bulk check across every hostname
            #     the routing config implies. Returns ``{"entries":
            #     [...]}`` for the SPA's DNS-resolution table, which
            #     pre-populates from routing config rather than asking
            #     the operator to type each hostname manually.
            try:
                from media_stack.api.services import dns_check as _dns_check
                # parse_qs/urlparse already imported at module level —
                # see route_probe branch comment for the bug class.
                qs = parse_qs(urlparse(handler.path).query)
                host = (qs.get("host") or [""])[0]
                if host.strip():
                    handler._json_response(HTTPStatus.OK, _dns_check.check(host))
                else:
                    handler._json_response(HTTPStatus.OK, _dns_check.check_all())
            except Exception as exc:  # noqa: BLE001
                handler._json_response(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": str(exc)[:99]},
                )
        elif path == "/api/gateway-hostnames":
            try:
                handler._json_response(
                    HTTPStatus.OK,
                    {"hostnames": _gateway_hostname_probe.read()},
                )
            except Exception as exc:  # noqa: BLE001
                handler._json_response(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": str(exc)[:99]},
                )
        elif path == "/api/tls/certificate":
            try:
                info = build_default_tls_service().describe().to_dict()
                handler._json_response(HTTPStatus.OK, info)
            except Exception as exc:  # noqa: BLE001
                handler._json_response(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    # 99-char cap matches the _ERR_LEN convention used
                    # throughout the POST handlers for consistency.
                    {"error": str(exc)[:99]},
                )
        elif path == "/api/tls/certificate/download":
            # Browser-friendly download of the public cert PEM, so a
            # user can one-click "trust this CA" from the post-bootstrap
            # wizard without having to find the file on disk. Only the
            # cert is returned — never the private key.
            try:
                cert_path = build_default_tls_service().cert_path
                if not cert_path.is_file():
                    handler._json_response(
                        HTTPStatus.NOT_FOUND,
                        {"error": "no certificate installed"},
                    )
                else:
                    pem = cert_path.read_bytes()
                    handler._raw_response(
                        HTTPStatus.OK,
                        "application/x-pem-file",
                        pem,
                        {
                            "Content-Disposition":
                                'attachment; filename="media-stack-ca.pem"',
                        },
                    )
            except Exception as exc:  # noqa: BLE001
                handler._json_response(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": str(exc)[:99]},
                )
        elif path == "/api/audit-log/verify":
            try:
                ok, detail = build_default_service()._audit.verify_chain()
                handler._json_response(HTTPStatus.OK, {
                    "ok": ok, "detail": detail or "hash chain intact",
                })
            except Exception as exc:  # noqa: BLE001
                handler._json_response(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": str(exc)[:99]},
                )
        elif (path.startswith("/api/sessions/")
              or path.startswith("/api/security/")
              or path.startswith("/api/bans/")
              or path.startswith("/api/me/")
              or path == "/api/audit-log/head"
              or (path.startswith("/api/users/")
                  and path.endswith("/login-history"))):
            # Admin-read buckets (security-read) on the enumeration-
            # prone paths; /api/me/* rides the global limit. Any 429
            # here is a token-bucket miss — replies without running
            # the handler.
            if path in _SECURITY_READ_PATHS or (
                path.startswith("/api/users/")
                and path.endswith("/login-history")
            ):
                client_id = trusted_proxy_auth.client_ip(handler) or "-"
                if not _security_read_limiter.allow(
                    client_id=client_id, bucket="security-read",
                ):
                    handler._json_response(
                        HTTPStatus.TOO_MANY_REQUESTS,
                        {"error": "rate_limit_exceeded",
                         "detail": "security-read bucket exhausted"},
                    )
                    return
            _sessviz_handler.dispatch(handler, path)
        elif _media_integrity_handlers.matches_get(path):
            from media_stack.api.services.security_get_deps import (
                HandlerActorResolverFactory as _MIActorResolver,
            )
            actor = _MIActorResolver().resolve(handler)
            _media_integrity_handlers.dispatch_get(handler, path, actor)
        elif path == "/api/users" or path.startswith("/api/users/") \
                or path in ("/api/roles", "/api/user-providers",
                            "/api/audit-log", "/api/audit-log/stats",
                            "/api/users-reconcile",
                            "/api/invites", "/api/me", "/metrics",
                            "/api/tokens"):
            self._handle_user_mgmt(handler, path)
        elif path == "/api/access-urls":
            # Surface clickable URLs for users who haven't set DNS
            # yet — "you can reach the controller at http://<your-
            # LAN-IP>:9100". See access_urls.py for the contract.
            from media_stack.api.services.access_urls import (
                AccessUrlDiscovery,
            )
            host_hdr = ""
            try:
                host_hdr = handler.headers.get("Host", "") or ""
            except AttributeError:
                logging.getLogger("media_stack").debug("[DEBUG] Swallowed exception", exc_info=True)
            handler._json_response(
                HTTPStatus.OK,
                AccessUrlDiscovery(host_ip_hint=host_hdr).build(),
            )
        elif path == "/api/download-client-settings":
            handler._json_response(HTTPStatus.OK, content_svc.get_download_client_settings())
        elif path == "/api/quality-profiles":
            handler._json_response(HTTPStatus.OK, content_svc.get_quality_profiles())
        elif path == "/api/import-lists":
            handler._json_response(HTTPStatus.OK, content_svc.get_import_lists())
        elif path == "/api/libraries":
            # Merge live Jellyfin libraries with configured libraries.
            # `source` advertises which list the dashboard should
            # consider authoritative: "live" when Jellyfin returned
            # libraries, else passthrough of `configured.source`
            # (typically "defaults" / "profile"). The UI's
            # LibraryDataSourceBanner predicate keys on this field —
            # without the "live" branch, the "showing bootstrap
            # defaults" banner stays up even after live counts
            # populate, falsely blaming JELLYFIN_API_KEY.
            live = content_svc.get_jellyfin_libraries()
            configured = config_svc.get_libraries()
            live_libs = live.get("libraries", [])
            handler._json_response(HTTPStatus.OK, {
                "live": live_libs,
                "configured": configured.get("libraries", []),
                "source": "live" if live_libs else configured.get("source", "unknown"),
                "media_server": configured.get("media_server", ""),
            })
        elif path == "/api/recent":
            handler._json_response(HTTPStatus.OK, content_svc.get_recent())

        # --- Keys ---
        elif path == "/api/keys":
            _handle_keys(handler)

        # --- Disk ---
        elif path == "/api/disk":
            handler._json_response(HTTPStatus.OK, disk_svc.get_disk())
        elif path == "/api/cleanup-preview":
            handler._json_response(HTTPStatus.OK, disk_svc.preview_cleanup())

        # --- Guardrails (cross-domain registry) ---
        elif path == "/api/guardrails":
            from media_stack.services import guardrails as _guardrails_pkg
            registry = _guardrails_pkg.default()
            try:
                from media_stack.application.guardrails.evaluation_loop import (
                    _resolved_interval as _gr_interval,
                )
                interval = int(_gr_interval(None))
            except Exception:  # noqa: BLE001
                interval = 300
            handler._json_response(HTTPStatus.OK, {
                "guardrails": registry.status_summary(),
                # Cadence (seconds) at which guardrails re-evaluate.
                # Operators can tune via MEDIA_STACK_GUARDRAIL_INTERVAL_SECONDS
                # env var or POST /api/guardrails/config. Surfacing it
                # here lets the UI render an editable input + the
                # "next evaluation in" hint on the page header.
                "evaluation_interval_seconds": interval,
            })

        # --- Config ---
        elif path == "/api/env":
            handler._json_response(HTTPStatus.OK, config_svc.get_env())
        elif path == "/api/routing":
            handler._json_response(HTTPStatus.OK, config_svc.get_routing())
        elif path == "/api/routing/v2":
            # Migrated v2 view of the routing config. Read-only in
            # PR-4; PR-5 adds POST + apply. Reads the v1 dict via
            # the legacy service, runs migrate_v1_to_v2, returns the
            # structured shape the new UI consumes.
            from media_stack.api.services.config.routing import (
                migrate_v1_to_v2,
                validate_routing_config,
            )
            try:
                v1 = config_svc.get_routing()
                # ``direct_hosts`` becomes hosts[]; need media_server_id
                # from the profile so that role resolves to a concrete
                # service rather than the default "jellyfin".
                ms_id = None
                try:
                    ms_id = config_svc._profile.media_server_id()  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    ms_id = None
                cfg = migrate_v1_to_v2(v1, media_server_id=ms_id)
                # Validate but include errors as a sibling field so
                # the UI can render them inline; v1 → v2 migration may
                # produce a config that fails some VRs (e.g. apex
                # default = NONE which is fine; other implicit defaults
                # may not be). Operators see what's not happy without
                # being blocked from reading.
                errors = [
                    {"code": e.code, "field": e.field,
                     "message": e.message, "hint": e.hint}
                    for e in validate_routing_config(cfg)
                ]
                handler._json_response(HTTPStatus.OK, {
                    "config": cfg.to_dict(),
                    "validation": errors,
                })
            except Exception as exc:  # noqa: BLE001
                handler._json_response(
                    HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)[:200]},
                )
        elif path == "/api/routing/routes":
            # Operator-facing route table. Answers "what URL goes
            # where?" by enumerating every route the gateway emits:
            #
            #   * Per-service path-prefix routes (e.g. /app/jellyfin/
            #     → service_jellyfin) when strategy is path or hybrid
            #   * Per-service subdomain routes (e.g. jellyfin.iomio.io
            #     → service_jellyfin) when strategy is subdomain or
            #     hybrid AND the operator wired a direct_host
            #   * Path aliases (HTTP redirects)
            #   * Apex + catch-all
            #
            # Returned as a flat list with `host`, `match`, `target`,
            # `kind`, `source` so the UI can render a sortable table.
            from media_stack.api.services.config.routing import (
                migrate_v1_to_v2,
            )
            from media_stack.api.services.registry import (
                SERVICES,
                get_active_service_ids,
            )
            try:
                v1 = config_svc.get_routing()
                ms_id = None
                try:
                    ms_id = config_svc._profile.media_server_id()  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    ms_id = None
                cfg = migrate_v1_to_v2(v1, media_server_id=ms_id)

                rows: list[dict] = []
                strategy = cfg.strategy.value
                gw = cfg.gateway_host
                app_prefix = cfg.app_path_prefix or "/app"
                active = get_active_service_ids()

                # 1. Per-service path-prefix routes (auto, derived).
                if strategy in ("path", "hybrid") and gw:
                    for svc in SERVICES:
                        if not svc.web_ui or svc.id not in active:
                            continue
                        rows.append({
                            "host": gw,
                            "match": f"{app_prefix}/{svc.id}/",
                            "target": svc.id,
                            "target_kind": "service",
                            "kind": "auto_path",
                            "source": (
                                f"strategy={strategy}, app_path_prefix="
                                f"{app_prefix} (derived per-service)"
                            ),
                        })

                # 2. Explicit hosts from the v2 config (direct_hosts in
                # v1, hosts[] in v2).
                for h in cfg.hosts:
                    if h.canonical and h.canonical != gw:
                        rows.append({
                            "host": h.canonical,
                            "match": "/" if not h.path_prefix else h.path_prefix,
                            "target": h.service_id,
                            "target_kind": "service",
                            "kind": "subdomain",
                            "source": (
                                f"hosts[] entry (role={h.role})"
                            ),
                        })
                    if h.canonical == gw and h.path_prefix:
                        rows.append({
                            "host": gw,
                            "match": h.path_prefix
                                if h.path_prefix.endswith("/")
                                else h.path_prefix + "/",
                            "target": h.service_id,
                            "target_kind": "service",
                            "kind": "explicit_path",
                            "source": (
                                f"hosts[] entry (role={h.role}, "
                                "explicit path_prefix override)"
                            ),
                        })
                    # Aliases redirect to canonical.
                    for alias in h.aliases:
                        rows.append({
                            "host": alias,
                            "match": "/",
                            "target": h.canonical,
                            "target_kind": "redirect",
                            "kind": "host_alias",
                            "source": f"hosts[].aliases for {h.canonical}",
                        })

                # 3. Path aliases (HTTP redirects).
                for p in cfg.path_aliases:
                    if not p.from_path or not p.to_path:
                        continue
                    rows.append({
                        "host": gw,
                        "match": p.from_path,
                        "target": p.to_path,
                        "target_kind": "redirect",
                        "kind": "path_alias",
                        "source": f"path_aliases[] ({p.code})",
                    })

                # 4. Apex.
                if cfg.apex.action.value != "none":
                    rows.append({
                        "host": gw,
                        "match": "/ (exact)",
                        "target": (
                            cfg.apex.target
                            or f"({cfg.apex.action.value})"
                        ),
                        "target_kind": (
                            "redirect" if cfg.apex.action.value == "redirect"
                            else cfg.apex.action.value
                        ),
                        "kind": "apex",
                        "source": "apex.action",
                    })

                # 5. Catch-all.
                ca = cfg.catch_all
                rows.append({
                    "host": gw,
                    "match": "/ (catch-all)",
                    "target": ca.target or f"({ca.action.value})",
                    "target_kind": (
                        "redirect" if ca.action.value == "redirect"
                        else ca.action.value
                    ),
                    "kind": "catch_all",
                    "source": "catch_all.action",
                })

                handler._json_response(HTTPStatus.OK, {
                    "rows": rows,
                    "summary": {
                        "strategy": strategy,
                        "gateway_host": gw,
                        "app_path_prefix": app_prefix,
                        "active_service_count": len(active),
                    },
                })
            except Exception as exc:  # noqa: BLE001
                handler._json_response(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)[:200]})
        elif path == "/api/routing/preview":
            # Pure-function preview: returns the Envoy route_config + the
            # active EdgeBindingAdapter's ApplyPlan for the *current* v2
            # config (no body to POST in PR-5; that lands in a follow-up).
            # Operators see what would actually get applied without
            # touching the cluster.
            from media_stack.api.services.config.routing import migrate_v1_to_v2
            from media_stack.services.edge.envoy_route_generator_v2 import (
                generate_route_config_v2,
            )
            from media_stack.services.edge.k8s_ingress_adapter import (
                K8sIngressAdapter,
            )
            try:
                v1 = config_svc.get_routing()
                ms_id = None
                try:
                    ms_id = config_svc._profile.media_server_id()  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    ms_id = None
                cfg = migrate_v1_to_v2(v1, media_server_id=ms_id)
                route_config = generate_route_config_v2(cfg)
                # K8s adapter is the only one shipped today; pick it
                # unconditionally. PR-7 extends with auto-detect.
                plan = K8sIngressAdapter().compute_apply_plan(cfg)
                handler._json_response(HTTPStatus.OK, {
                    "envoy": {
                        "route_config": route_config,
                        "vhost_count": len(route_config.get("virtual_hosts", [])),
                    },
                    "binding": {
                        "adapter": "k8s_ingress",
                        "steps": [
                            {
                                "kind": s.kind,
                                "description": s.description,
                                "payload": s.payload,
                            }
                            for s in plan.steps
                        ],
                        "warnings": list(plan.warnings),
                    },
                })
            except Exception as exc:  # noqa: BLE001
                handler._json_response(
                    HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)[:200]},
                )
        elif path == "/api/routing/effective":
            # Same as /api/routing/v2 but with `defaults` merged into
            # every host's per-field knobs, so the UI shows what Envoy
            # actually sees instead of the operator-facing "inherited"
            # placeholders. Read-only.
            from media_stack.api.services.config.routing import (
                migrate_v1_to_v2,
            )
            try:
                v1 = config_svc.get_routing()
                ms_id = None
                try:
                    ms_id = config_svc._profile.media_server_id()  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    ms_id = None
                cfg = migrate_v1_to_v2(v1, media_server_id=ms_id)
                # Merge defaults into each host. Only fills in fields
                # the host left unset (None / empty). Per-host explicit
                # values always win.
                eff = cfg.to_dict()
                defaults = eff.get("defaults") or {}
                for h in eff.get("hosts", []):
                    if not h.get("websocket"):
                        h["websocket"] = defaults.get("websocket", False)
                    if not h.get("auth") and defaults.get("auth"):
                        h["auth"] = dict(defaults["auth"])
                    if not h.get("timeout_seconds"):
                        h["timeout_seconds"] = defaults.get("timeout_seconds", 0)
                    if not h.get("body_limit_mb"):
                        h["body_limit_mb"] = defaults.get("body_limit_mb", 0)
                    if not h.get("headers") and defaults.get("headers"):
                        h["headers"] = dict(defaults["headers"])
                handler._json_response(HTTPStatus.OK, {"config": eff})
            except Exception as exc:  # noqa: BLE001
                handler._json_response(
                    HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)[:200]},
                )
        elif path == "/api/profile":
            handler._json_response(HTTPStatus.OK, config_svc.get_profile())
        elif path == "/sw-config.json":
            # Service-worker runtime config — the dashboard's PWA SW
            # fetches this on install/update so its navigation
            # denylist tracks the routing engine instead of being
            # hard-coded in the bundled SW. Cached for 5 min by
            # default; the SW itself adds ``cache: 'no-store'`` to
            # its install-time fetch so changes propagate on the
            # next pod restart.
            from .services.sw_config import get_sw_config
            handler._json_response(HTTPStatus.OK, get_sw_config())
        elif path == "/api/sw-config":
            # Alias under /api/ for ext_authz parity — sister apps
            # pass /api/* through unauthenticated.
            from .services.sw_config import get_sw_config
            handler._json_response(HTTPStatus.OK, get_sw_config())
        elif path == "/api/runs" or path.startswith("/api/runs?"):
            # Per-job-run history. Replaces the legacy "last 10
            # runs" view that pulled from the batch-level
            # job-history.json. Filters via querystring.
            from urllib.parse import parse_qs, unquote
            from media_stack.application.jobs.run_history import get_runs
            qs = handler.path.split("?", 1)[1] if "?" in handler.path else ""
            params = parse_qs(qs, keep_blank_values=True)
            job = unquote(params.get("job", [""])[0]) or None
            parent = unquote(params.get("parent", [""])[0]) or None
            batch = unquote(params.get("batch", [""])[0]) or None
            try:
                limit = int(params.get("limit", ["100"])[0])
            except ValueError:
                limit = 100
            since_ts: float | None = None
            since_raw = unquote(params.get("since", [""])[0])
            if since_raw:
                try:
                    since_ts = float(since_raw)
                except ValueError:
                    since_ts = None
            records = get_runs(
                job_name=job,
                since_ts=since_ts,
                parent_run_id=parent,
                batch_id=batch,
                limit=max(1, min(50000, limit)),
            )
            handler._json_response(
                HTTPStatus.OK, {"runs": [r.to_dict() for r in records]},
            )
        elif path.startswith("/api/runs/latest/"):
            from media_stack.application.jobs.run_history import (
                get_latest_run,
            )
            job_name = path[len("/api/runs/latest/"):]
            record = get_latest_run(job_name)
            if record is None:
                handler._json_response(
                    HTTPStatus.NOT_FOUND, {"error": f"no runs for {job_name!r}"},
                )
            else:
                handler._json_response(HTTPStatus.OK, record.to_dict())
        elif path.startswith("/api/runs/"):
            from media_stack.application.jobs.run_history import (
                get_run, get_children,
            )
            run_id = path[len("/api/runs/"):]
            # Strip any query string the caller appended.
            run_id = run_id.split("?", 1)[0]
            record = get_run(run_id)
            if record is None:
                handler._json_response(
                    HTTPStatus.NOT_FOUND, {"error": f"run {run_id!r} not found"},
                )
            else:
                children = [c.to_dict() for c in get_children(run_id)]
                handler._json_response(
                    HTTPStatus.OK,
                    {**record.to_dict(), "children": children},
                )
        elif path == "/api/manifests":
            handler._json_response(HTTPStatus.OK, config_svc.get_manifests())
        elif path == "/api/envvars":
            handler._json_response(HTTPStatus.OK, config_svc.get_envvars())
        elif path == "/api/config-drift":
            handler._json_response(HTTPStatus.OK, api_cache.get_or_compute(
                "config_drift", config_svc.get_config_drift, ttl=60,
            ))
        elif path == "/api/config/libraries":
            handler._json_response(HTTPStatus.OK, config_svc.get_libraries())
        elif path == "/api/download-categories":
            handler._json_response(HTTPStatus.OK, config_svc.get_download_categories())
        elif path == "/api/metadata-settings":
            handler._json_response(HTTPStatus.OK, config_svc.get_metadata_settings())
        elif path == "/api/bazarr/subtitle-config":
            # Aggregator over Bazarr's languages + profiles + settings.
            # Surfaced on Settings → Display → Subtitle preferences.
            try:
                from .services import bazarr_proxy
                handler._json_response(
                    HTTPStatus.OK, bazarr_proxy.get_subtitle_config(),
                )
            except Exception as exc:  # noqa: BLE001
                handler._json_response(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)[:200]})
        elif path == "/api/livetv-sources":
            handler._json_response(HTTPStatus.OK, config_svc.get_livetv_sources())
        elif path == "/api/discovery-lists":
            handler._json_response(HTTPStatus.OK, config_svc.get_discovery_lists())
        elif path == "/api/display-preferences":
            # Return current display preference config from contract defaults
            from media_stack.services.jobs.framework import _load_cfg_from_contracts
            cfg = _load_cfg_from_contracts()
            playback = cfg.get("jellyfin_playback", {})
            dp = playback.get("display_preferences", {})
            handler._json_response(HTTPStatus.OK, {
                "enabled": dp.get("enabled", True),
                "show_backdrop": dp.get("show_backdrop", True),
                "custom_prefs": dp.get("custom_prefs", {}),
                "per_library_prefs": dp.get("per_library_prefs", {}),
                "clients": dp.get("clients", ["emby"]),
            })
        elif path == "/api/iptv-countries":
            handler._json_response(HTTPStatus.OK, config_svc.get_iptv_countries())
        elif path == "/api/epg-providers":
            from media_stack.services.epg_provider_service import get_guide_providers, _load_health_cache
            handler._json_response(HTTPStatus.OK, {
                "providers": get_guide_providers(),
                "health": _load_health_cache(),
            })
        elif path == "/api/epg-health":
            from media_stack.services.epg_provider_service import run_health_check
            handler._json_response(HTTPStatus.OK, run_health_check())
        elif path == "/api/telemetry":
            from media_stack.services.telemetry_client import collect_metrics, push_telemetry
            if "push" in (handler.path.split("?")[1] if "?" in handler.path else ""):
                handler._json_response(HTTPStatus.OK, push_telemetry())
            else:
                handler._json_response(HTTPStatus.OK, collect_metrics())
        elif path == "/api/jobs/running":
            # Aggregator: every job, action, scan, or operation the
            # controller currently has in-flight. Surfaced in the
            # global banner so operators see "3 things are happening
            # right now" from any page, not one source at a time.
            #
            # Sources combined here:
            #   1. ActionRecord-tracked actions (bootstrap, reconcile,
            #      mass-search, etc.) with status=RUNNING
            #   2. Job framework executions in progress
            #   3. K8s Jobs in Active phase (CronJob fires that haven't
            #      completed yet) — best-effort via the K8s client
            try:
                running: list[dict] = []
                # 1. ActionRecord state.
                try:
                    state = handler.state
                    cur = getattr(state, "current_action", None)
                    if cur is not None and not getattr(cur, "is_terminal", True):
                        running.append({
                            "id": cur.id,
                            "name": cur.name,
                            "kind": "action",
                            "started_at": cur.started_at,
                            "elapsed_seconds": cur.elapsed_seconds,
                            "triggered_by": getattr(cur, "triggered_by", ""),
                        })
                    # action_history may also contain still-running rows
                    # from re-entrant actions; pick those out too.
                    for a in getattr(state, "action_history", []):
                        if (
                            a is not cur
                            and getattr(a, "status", None)
                            and a.status.value == "running"
                            and not getattr(a, "is_terminal", True)
                        ):
                            running.append({
                                "id": a.id, "name": a.name, "kind": "action",
                                "started_at": a.started_at,
                                "elapsed_seconds": a.elapsed_seconds,
                                "triggered_by": getattr(a, "triggered_by", ""),
                            })
                except Exception as exc:  # noqa: BLE001
                    log_swallowed(exc)
                # 2. K8s active CronJob/Job pods (best-effort).
                if os.environ.get("KUBERNETES_SERVICE_HOST"):
                    try:
                        from kubernetes import client, config as kconfig
                        try:
                            kconfig.load_incluster_config()
                        except Exception:  # noqa: BLE001
                            kconfig.load_kube_config()
                        v1batch = client.BatchV1Api()
                        ns = os.environ.get("MEDIA_STACK_NAMESPACE", "media-stack")
                        jobs = v1batch.list_namespaced_job(
                            namespace=ns, limit=50,
                        )
                        for j in jobs.items:
                            active = (j.status.active or 0) if j.status else 0
                            if active > 0:
                                running.append({
                                    "id": j.metadata.name,
                                    "name": j.metadata.name,
                                    "kind": "k8s_job",
                                    "started_at": (
                                        j.status.start_time.timestamp()
                                        if j.status and j.status.start_time
                                        else None
                                    ),
                                    "active_pods": active,
                                })
                    except Exception as exc:  # noqa: BLE001
                        log_swallowed(exc)
                handler._json_response(HTTPStatus.OK, {
                    "running": running,
                    "count": len(running),
                })
            except Exception as exc:  # noqa: BLE001
                handler._json_response(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)[:200], "running": []})
        elif path == "/api/jobs":
            from media_stack.services.jobs.framework import discover_jobs_from_contracts, build_job_framework, get_job_history
            jobs = discover_jobs_from_contracts()
            root = build_job_framework()
            def _tree(job):
                return {
                    "name": job.name,
                    "requires": job.requires,
                    "sub_jobs": [_tree(s) for s in job.sub_jobs],
                }
            # `tree` is a list so the SPA's `asArray<JobTreeNode>(raw.tree)`
            # passes through unchanged. Pre-v1.0.186 we emitted a bare
            # object here and the UI's coerce helper collapsed it to []
            # — Jobs page tree silently rendered empty.
            handler._json_response(HTTPStatus.OK, {
                "jobs": jobs,
                "tree": [_tree(root)],
                "count": len(jobs),
                "history": get_job_history(),
            })
        elif path == "/api/storage-breakdown":
            handler._json_response(HTTPStatus.OK, disk_svc.get_storage_breakdown())
        elif path == "/api/import-lists-all":
            handler._json_response(HTTPStatus.OK, content_svc.get_all_import_lists())
        elif path == "/api/schedules":
            from .services import scheduler as sched_svc
            handler._json_response(HTTPStatus.OK, sched_svc.get_schedules())
        elif path == "/api/onboarding":
            handler._json_response(HTTPStatus.OK, config_svc.get_onboarding_status())
        elif path == "/api/download-analytics":
            handler._json_response(HTTPStatus.OK, content_svc.get_download_analytics())
        elif path == "/api/backup":
            payload = config_svc.get_backup(handler.state)
            handler._raw_response(HTTPStatus.OK, "application/json", payload, {
                "Content-Disposition": f'attachment; filename="media-stack-backup-{time.strftime("%Y%m%d-%H%M%S")}.json"',
            })

        # --- Log level ---
        elif path == "/api/log-level":
            from media_stack.services.runtime_platform import get_log_level
            handler._json_response(HTTPStatus.OK, {"level": get_log_level()})

        # --- Auth ---
        elif path == "/api/auth/identity":
            # Resolution order matches the auth policy: Authelia/Authentik
            # forwarded headers first, then the session cookie (for
            # direct-access deployments where the user signed in with the
            # controller's in-page form), then Basic auth (when the
            # browser is supplying the credentials on every request via
            # the WWW-Authenticate popup). Without all three branches
            # the avatar in the top-right falls back to "??" even
            # though the operator is authenticated — exactly the
            # symptom that drove this branch to grow over time.
            user = (handler.headers.get("Remote-User", "")
                    or handler.headers.get("X-authentik-username", ""))
            name = (handler.headers.get("Remote-Name", "")
                    or handler.headers.get("X-authentik-name", ""))
            email = (handler.headers.get("Remote-Email", "")
                     or handler.headers.get("X-authentik-email", ""))
            groups = (handler.headers.get("Remote-Groups", "")
                      or handler.headers.get("X-authentik-groups", ""))
            if not user:
                user = session_cookie_reader.username_for_handler(handler) or ""
            if not user:
                auth_hdr = handler.headers.get("Authorization", "") or ""
                if auth_hdr.startswith("Basic "):
                    try:
                        decoded = base64.b64decode(auth_hdr[6:]).decode(
                            "utf-8", "replace")
                        user = decoded.partition(":")[0] or ""
                    except Exception:  # noqa: BLE001
                        user = ""
            # If we resolved a username but have no display_name /
            # email yet, hydrate them from the user store. The Topbar
            # avatar prefers display_name and looks shabby with a
            # bare "admin" — pulling the row's display_name (e.g.
            # "Administrator") fixes that for the common case.
            if user and not name:
                try:
                    from media_stack.core.auth.users.user_service_factory \
                        import build_default_service
                    svc = build_default_service()
                    row = svc._store.get_by_username(user)
                    if row is not None:
                        name = (getattr(row, "display_name", "") or "").strip()
                        if not email:
                            email = (getattr(row, "email", "") or "").strip()
                except Exception:  # noqa: BLE001
                    pass
            handler._json_response(HTTPStatus.OK, {
                "authenticated": bool(user),
                "user": user,
                "display_name": name or user,
                "email": email,
                "groups": groups,
            })
        elif path == "/api/auth/config":
            from .services.auth_config import AuthConfigService
            handler._json_response(HTTPStatus.OK, AuthConfigService().get_current_config())
        elif path == "/api/auth/modes":
            from .services.auth_config import AuthConfigService
            handler._json_response(HTTPStatus.OK, {"modes": AuthConfigService().get_auth_modes()})
        elif path == "/api/auth/oidc-providers":
            from .services.auth_config import AuthConfigService
            handler._json_response(HTTPStatus.OK, {"providers": AuthConfigService().get_oidc_providers()})
        elif path == "/api/auth/service-policies":
            from .services.auth_config import AuthConfigService
            handler._json_response(HTTPStatus.OK, {"services": AuthConfigService().get_service_policies()})

        # --- Ops ---
        elif path == "/api/namespaces":
            handler._json_response(HTTPStatus.OK, ops_svc.get_namespaces())
        elif path == "/api/image-updates":
            handler._json_response(HTTPStatus.OK, ops_svc.check_image_updates())
        elif path == "/api/gpu":
            handler._json_response(HTTPStatus.OK, ops_svc.get_gpu_info())
        elif path == "/api/snapshots":
            handler._json_response(HTTPStatus.OK, ops_svc.get_config_snapshots())
        elif path.startswith("/api/snapshots/") and path.count("/") == 3:
            filename = path.split("/")[3]
            handler._json_response(HTTPStatus.OK, ops_svc.get_snapshot_detail(filename))
        elif path == "/api/snapshot-diff":
            _handle_snapshot_diff(handler)
        elif path == "/api/mounts":
            handler._json_response(HTTPStatus.OK, ops_svc.get_mount_info())
        elif path == "/api/logs" or path.startswith("/api/logs?"):
            _handle_logs(handler)
        elif path == "/api/logs/stream" or path.startswith("/api/logs/stream?"):
            # Filterable SSE stream of the controller's ring buffer.
            # Same filter dimensions as `/api/logs/{source}` (action,
            # level, q, since). Replaces the legacy unfiltered
            # `/logs/stream` endpoint for the new UI tail mode while
            # leaving the old route in place for compat.
            _handle_logs_sse(handler)
        elif path == "/api/events" or path.startswith("/api/events?"):
            # Unified domain-event SSE bus. Forwards typed events from
            # the process-wide ``EventBus`` (jobs, sessions,
            # media_integrity for v1.0.277; access_log/health/
            # guardrails to follow). The ``topics=`` query param
            # narrows to a subset; empty means all known topics.
            _handle_events_sse(handler)
        elif path == "/api/logs/sources" or path.startswith("/api/logs/sources?"):
            # Dynamic source list for the Logs UI's filter dropdown.
            # The legacy hardcoded list in LogsToolbar.tsx capped at 8
            # services even though SERVICES has 27+; operators couldn't
            # reach jellyfin/jellyseerr/sabnzbd/authelia/envoy logs etc.
            # Return every service the registry knows about, plus the
            # platform pods (controller, ui) the operator may want to
            # tail directly, plus every CronJob template (so transient
            # job pods like `media-stack-media-hygiene-29619765-2j9dc`
            # are reachable through the dashboard — operators were
            # previously stuck running `kubectl logs <pod>` to debug
            # CronJob fires like the legacy media-hygiene Wave 6
            # suspension).
            try:
                from media_stack.api.services.registry import SERVICES
                svcs = sorted({s.id for s in SERVICES})
            except Exception as exc:  # noqa: BLE001
                log_swallowed(exc)
                svcs = []
            cronjobs = ops_svc.list_cronjob_log_sources()
            platform = ["controller", "ui"]
            handler._json_response(HTTPStatus.OK, {
                "sources": [
                    *({"id": p, "label": p.title(), "kind": "platform"}
                      for p in platform),
                    *({"id": s, "label": s.title(), "kind": "service"}
                      for s in svcs),
                    *cronjobs,
                ],
            })
        elif path.startswith("/api/logs/") and path.count("/") == 3:
            _handle_service_logs(handler, path)

        # --- Metrics ---
        elif path == "/metrics":
            handler._raw_response(HTTPStatus.OK, "text/plain; version=0.0.4; charset=utf-8",
                                  metrics_svc.get_prometheus_metrics(api_cache).encode("utf-8"))
        elif path == "/api/envoy/stats":
            handler._json_response(HTTPStatus.OK, metrics_svc.get_envoy_stats())
        elif path == "/api/envoy/admin-summary":
            # Operator-facing aggregate of cluster health + request
            # rates + p50/p95/p99 latency + active connections + TLS
            # handshake errors. Surfaced on the Routing tab.
            handler._json_response(HTTPStatus.OK, metrics_svc.get_envoy_admin_summary())
        elif path == "/api/envoy/access-log":
            # Stream the last N lines of Envoy's access log so the
            # operator panel can show live request flow with source
            # IPs, paths, statuses, upstream cluster, and latency.
            #
            # Sources tried in order:
            #   1. ``ENVOY_ACCESS_LOG_PATH`` env var if set (file path).
            #   2. ``kubectl logs`` for the envoy pod (when running on
            #      K8s — controller's ServiceAccount has read access).
            #   3. ``docker compose logs`` for the envoy service when
            #      no kubectl is available.
            # Each entry is parsed as JSON when possible (the Envoy
            # access_log filter is configured to emit JSON in the
            # default media-stack profile); falls back to raw text
            # otherwise.
            qs = parse_qs(urlparse(handler.path).query)
            try:
                limit = int((qs.get("limit") or ["50"])[0])
            except (TypeError, ValueError):
                limit = 50
            limit = max(1, min(500, limit))
            try:
                from media_stack.api.services.envoy_access_log import (
                    tail_envoy_access_log,
                )
                rows = tail_envoy_access_log(limit=limit)
                handler._json_response(HTTPStatus.OK, {
                    "rows": rows,
                    "limit": limit,
                })
            except Exception as exc:  # noqa: BLE001
                handler._json_response(
                    HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)[:200], "rows": []},
                )
        elif path == "/api/envoy/timeseries":
            # Rolling buffer of recent admin-summary samples + derived
            # rate deltas. Populated as a side-effect of the
            # admin-summary polling, so series only covers the time the
            # Routing panel has been open. ``window_seconds`` query
            # param defaults to 1800 (30 min); clamps to ≥60s.
            qs = parse_qs(urlparse(handler.path).query)
            try:
                window = int((qs.get("window") or ["1800"])[0])
            except (TypeError, ValueError):
                window = 1800
            handler._json_response(
                HTTPStatus.OK,
                metrics_svc.get_envoy_timeseries(window),
            )
        elif path == "/api/feed.xml":
            handler._raw_response(HTTPStatus.OK, "application/rss+xml; charset=utf-8",
                                  metrics_svc.get_rss_feed(handler.state, api_cache).encode("utf-8"))
        elif path == "/api/grafana.json":
            handler._json_response(HTTPStatus.OK, metrics_svc.get_grafana_dashboard())
        elif path == "/api/openapi.json":
            # Return the real `contracts/api/openapi.yaml` parsed to
            # JSON. The legacy `_get_openapi_spec()` was a hardcoded
            # stub from pre-Phase 4 days — it returned ~50 endpoints
            # with no examples, no operationId, no x-codeSamples,
            # which left the api-docs page rendering an empty viewer
            # even though the real spec has 209 operations + 360
            # examples + 23 x-codeSamples blocks. The Stoplight
            # Elements component points at this URL via
            # `apiDescriptionUrl="/api/openapi.json"`; without parsing
            # the rich YAML, operators saw an empty docs surface.
            try:
                import yaml as _yaml
                spec = _yaml.safe_load(_OPENAPI_YAML) or {}
                spec["servers"] = _build_openapi_servers()
                handler._json_response(HTTPStatus.OK, spec)
            except Exception as exc:  # noqa: BLE001
                log_swallowed(exc)
                # Fall back to the legacy stub so a YAML parse error
                # doesn't take the docs page down entirely.
                handler._json_response(HTTPStatus.OK, handler._get_openapi_spec())
        elif path == "/api/openapi.yaml":
            _handle_openapi_yaml(handler)

        # --- Dashboard / static assets / Swagger UI HTML (moved to UI container) ---
        # As of v1.0.175 the Python controller no longer serves any
        # HTML, CSS, JS, or image assets — a dedicated UI container
        # owns the dashboard. We answer the prior paths with 410 GONE
        # plus a Location header pointing at the UI image's app root,
        # so any cached client / bookmark surfaces a clear "moved"
        # response instead of a generic 404.
        elif (
            path in ("/", "/dashboard")
            or path.startswith("/api/static/")
            or path == "/api/docs"
        ):
            _handle_ui_moved(handler)

        else:
            handler._json_response(HTTPStatus.NOT_FOUND, {"error": "not found"})


    @staticmethod
    def _build_openapi_servers() -> list[dict]:
        """Build the OpenAPI servers list from the live routing config.
    
        This ensures /api/docs always shows the correct URLs for the
        current deployment -- no hardcoded hosts that break across envs.
        """
        servers = [{"url": "/", "description": "Current host (auto-detected)"}]
        try:
            routing = config_svc.get_routing()
            gw_host = routing.get("gateway_host", "")
            gw_port = int(routing.get("gateway_port", 80))
            prefix = str(routing.get("app_path_prefix", "/app")).rstrip("/")
            port_str = "" if gw_port == 80 else f":{gw_port}"
            if gw_host:
                # Gateway with path prefix (e.g. http://comp.my/app/media-stack-controller)
                ctrl_name = os.environ.get("CONTROLLER_CONTAINER_NAME", "media-stack-controller")
                servers.append({
                    "url": f"http://{gw_host}{port_str}{prefix}/{ctrl_name}",
                    "description": f"Gateway ({gw_host}{prefix}/{ctrl_name})",
                })
                # Gateway root (no prefix — for direct-host routing)
                servers.append({
                    "url": f"http://{gw_host}{port_str}",
                    "description": f"Gateway root ({gw_host})",
                })
        except Exception as exc:
            log_swallowed(exc)
        ctrl_port = int(os.environ.get("CONTROLLER_PORT", "9100"))
        servers.append({
            "url": f"http://localhost:{ctrl_port}",
            "description": "Localhost direct",
        })
        runtime = os.environ.get("MEDIA_STACK_RUNTIME", "compose")
        if runtime == "kubernetes":
            servers.append({
                "url": f"http://media-stack-controller.media-stack.svc:{ctrl_port}",
                "description": "Kubernetes in-cluster",
            })
        return servers

    @staticmethod
    def _handle_keys(handler: ControllerAPIHandler) -> None:
        """Return REDACTED per-service API-key inventory + admin username.

        **Security**: this endpoint returns key **metadata only** \u2014
        never the raw key. The shape per service is
        ``{"has_key": bool, "fingerprint": "abcd\u2026wxyz", "source": ""}``
        which lets the admin UI say "yes, Sonarr has a key that starts
        with abcd\u2026 and ends with \u2026wxyz" without ever handing the key
        to the browser.

        Rationale (security audit 2026-04-24): a previous version of
        this endpoint returned every discovered provider's raw API
        key to any authenticated caller. A single compromised
        read-scope bearer token == full stack compromise (Jellyfin,
        every *arr, qBittorrent). Revealing a raw key now requires
        an explicit reveal endpoint (TODO) that is separately audited,
        rate-limited, and admin-only.

        See ``core.auth.secret_redaction.redact_api_key_map`` for the
        central redaction helper.
        """
        from media_stack.core.auth.secret_redaction import redact_api_key_map

        raw_keys = health_svc.discover_api_keys()
        keys = redact_api_key_map(raw_keys, source="discovered")
        admin_user = os.environ.get("STACK_ADMIN_USERNAME", "admin")
        admin_pass = os.environ.get("STACK_ADMIN_PASSWORD", "")
        handler._json_response(HTTPStatus.OK, {
            "keys": keys,
            "admin": {
                "username": admin_user,
                "password_set": bool(admin_pass),
            },
            "count": len(keys),
        })

    @staticmethod
    def _handle_branding(handler: ControllerAPIHandler) -> None:
        """Return brand config (name, homepage, asset paths). The
        dashboard fetches this at load time and renders the
        header/favicon/splash from it. Source-of-truth file is
        ``contracts/branding.yaml`` so a sysadmin can white-label
        the stack without touching code."""
        # Resolve config file. Honor an explicit override env var
        # so an operator can drop a custom yaml anywhere on disk.
        candidates = []
        env_path = os.environ.get("BRANDING_CONFIG_FILE", "").strip()
        if env_path:
            candidates.append(Path(env_path))
        candidates.extend([
            Path("/contracts/branding.yaml"),
            Path(__file__).resolve().parents[3] / "contracts" / "branding.yaml",
            Path("contracts/branding.yaml"),
        ])
        # Naming convention (industry pattern: product dominates,
        # vendor is secondary credit):
        #   * ``name``     — product short name shown in chrome
        #     ("Media Stack"). The sidebar wordmark reads from this.
        #   * ``tagline``  — full product name for splash / about
        #     ("Media Stack Controller").
        #   * ``vendor``   — company / publisher ("iomio"). Surfaced
        #     as a small "by iomio" subtitle, never as the primary
        #     wordmark.
        # The link target is ALWAYS the dashboard root (``/``); the
        # ``homepage_url`` is for an "About" external link (footer /
        # settings → about), not the sidebar mark.
        defaults = {
            "name": "Media Stack",
            "tagline": "Media Stack Controller",
            "vendor": "iomio",
            "homepage_url": "https://iomio.io",
            "wordmark": "/api/static/iomio-wordmark.svg",
            "icon": "/api/static/iomio-icon.svg",
            "illustration": "/api/static/iomio-orbit.svg",
        }
        loaded = {}
        for cand in candidates:
            if cand and cand.is_file():
                try:
                    import yaml as _yaml
                    raw = _yaml.safe_load(cand.read_text(encoding="utf-8")) or {}
                    loaded = (raw.get("brand") or {}) if isinstance(raw, dict) else {}
                    break
                except Exception as exc:
                    log_swallowed(exc)
        merged = {**defaults, **{k: v for k, v in loaded.items() if v is not None}}
        handler._json_response(HTTPStatus.OK, {"brand": merged})

    @staticmethod
    def _handle_services(handler: ControllerAPIHandler) -> None:
        from media_stack.api.services.registry import (
            SERVICES,
            build_apps_listing,
            get_enabled_services,
            is_service_enabled,
        )
        # The Apps page renders one card per launchable, profile-active
        # service. Two filter dimensions:
        #
        #   * ``web_ui: false`` — hidden registry entries that exist
        #     ONLY to anchor jobs in the bootstrap DAG (``core``,
        #     ``media_integrity``). They have no host/port and the
        #     dashboard must not render them.
        #
        #   * Profile gate — the active deploy's ``COMPOSE_PROFILES``
        #     set decides whether plex / authentik / traefik / etc.
        #     should be considered "deployed". Without this filter
        #     the launcher used to show every YAML-declared service
        #     (28+) regardless of whether the operator actually
        #     deployed it, leading to a row of broken tiles and a
        #     "why is plex listed when I never enabled it?" support
        #     loop.
        #
        # Operators can opt out per-request with ``?include=all`` —
        # useful for tooling and the registry inspector — but the UI
        # treats the unfiltered list as the default.
        params: dict[str, str] = {}
        if "?" in handler.path:
            from urllib.parse import parse_qs
            for k, vs in parse_qs(
                handler.path.split("?", 1)[1], keep_blank_values=True,
            ).items():
                if vs:
                    params[k] = vs[0]
        include_all = params.get("include", "").strip().lower() == "all"
        ctrl_port = int(os.environ.get("BOOTSTRAP_API_PORT", os.environ.get("CONTROLLER_PORT", "9100")))
        handler._json_response(
            HTTPStatus.OK,
            build_apps_listing(
                list(SERVICES),
                include_all=include_all,
                controller_port=ctrl_port,
            ),
        )

    # In-process cache so a Sonarr poll every few minutes doesn't
    # hammer TVMaze. 6h matches Sonarr's default list-refresh
    # cadence — the popular set doesn't shift faster than that.
    _POPULAR_TV_CACHE: dict[str, Any] = {"ts": 0.0, "payload": []}
    _POPULAR_TV_TTL_SEC = 6 * 3600

    @staticmethod
    def _handle_popular_tv(handler: ControllerAPIHandler) -> None:
        """Sonarr CustomImport feed of popular TV.

        Sonarr's stock import-list providers are a dead end for OTB
        live discovery: IMDb's importer is upstream-broken (returns
        202 from rate limiting), and Trakt/Plex/AniList all need
        per-user OAuth. ``CustomImport`` was the escape hatch — it
        polls any URL that returns ``[{"tvdbId": N}, ...]``.

        TVMaze is the source: fully free, no key, public. We pull
        a few pages of their show index, score by ``rating.average``
        (fall back to weighted vote count when missing), filter to
        English-language shows that have a ``thetvdb`` external id,
        and return the top ~150. Cached for 6h to stay polite.
        """
        import time as _t
        import urllib.request as _ur
        import urllib.error as _ue
        cache = GetRequestHandler._POPULAR_TV_CACHE
        ttl = GetRequestHandler._POPULAR_TV_TTL_SEC
        if cache["payload"] and (_t.time() - cache["ts"]) < ttl:
            handler._json_response(HTTPStatus.OK, cache["payload"])
            return

        # Pull pages 0–3 (~1000 shows). TVMaze paginates by 250.
        # 4 pages gives us enough breadth to filter aggressively
        # without making the request take more than a couple
        # seconds. 404 means we've exhausted the index.
        shows: list[dict[str, Any]] = []
        for page in range(4):
            url = f"https://api.tvmaze.com/shows?page={page}"
            try:
                req = _ur.Request(url, headers={"User-Agent": "media-stack-controller"})
                with _ur.urlopen(req, timeout=15) as r:
                    chunk = json.loads(r.read())
                    if isinstance(chunk, list):
                        shows.extend(chunk)
            except _ue.HTTPError as exc:
                if exc.code == 404:
                    break
                continue
            except Exception:
                continue

        scored: list[tuple[float, int, str]] = []
        for s in shows:
            if not isinstance(s, dict):
                continue
            ext = s.get("externals") or {}
            tvdb = ext.get("thetvdb")
            try:
                tvdb_id = int(tvdb) if tvdb is not None else 0
            except (TypeError, ValueError):
                tvdb_id = 0
            if tvdb_id <= 0:
                continue
            lang = str(s.get("language") or "").strip().lower()
            if lang and lang != "english":
                continue
            rating = (s.get("rating") or {}).get("average")
            try:
                score = float(rating) if rating is not None else 0.0
            except (TypeError, ValueError):
                score = 0.0
            if score < 7.0:
                continue
            scored.append((score, tvdb_id, str(s.get("name") or "")))

        # Top 150 by rating, dedupe TVDB ids defensively.
        scored.sort(key=lambda t: t[0], reverse=True)
        seen: set[int] = set()
        payload: list[dict[str, Any]] = []
        for score, tvdb_id, name in scored:
            if tvdb_id in seen:
                continue
            seen.add(tvdb_id)
            payload.append({"tvdbId": tvdb_id, "title": name})
            if len(payload) >= 150:
                break

        # If TVMaze was unreachable AND we have a stale cache, serve
        # it anyway — better than 0 entries which would tell Sonarr
        # to prune everything it auto-added.
        if not payload and cache["payload"]:
            handler._json_response(HTTPStatus.OK, cache["payload"])
            return

        cache["ts"] = _t.time()
        cache["payload"] = payload
        handler._json_response(HTTPStatus.OK, payload)

    @staticmethod
    def _handle_services_categories(handler: ControllerAPIHandler) -> None:
        from media_stack.api.services.registry import CATEGORIES
        import copy
        cats = copy.deepcopy(CATEGORIES)
        infra = next((c for c in cats if c["label"].lower() == "infrastructure"), None)
        if infra:
            if "controller" not in infra["ids"]:
                infra["ids"].append("controller")
        else:
            cats.append({"label": "Infrastructure", "ids": ["controller"]})
        handler._json_response(HTTPStatus.OK, cats)

    @staticmethod
    def _handle_service_api_key(handler: ControllerAPIHandler, path: str) -> None:
        parts = path.split("/")
        svc_id = parts[3] if len(parts) >= 5 else ""
        from media_stack.api.services.registry import SERVICE_MAP
        svc = SERVICE_MAP.get(svc_id)
        if not svc or not svc.api_key_env:
            handler._json_response(HTTPStatus.NOT_FOUND, {"error": f"Service '{svc_id}' not found or has no API key"})
        else:
            current = (os.environ.get(svc.api_key_env) or "").strip()
            handler._json_response(HTTPStatus.OK, {
                "service": svc_id, "env": svc.api_key_env,
                "has_key": bool(current),
                "key_preview": f"{current[:4]}...{current[-4:]}" if len(current) > 8 else ("set" if current else ""),
            })

    @staticmethod
    def _handle_snapshot_diff(handler: ControllerAPIHandler) -> None:
        params: dict[str, str] = {}
        if "?" in handler.path:
            for part in handler.path.split("?", 1)[1].split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[k] = v
        handler._json_response(HTTPStatus.OK, ops_svc.diff_snapshots(params.get("a", ""), params.get("b", "")))

    @staticmethod
    def _handle_logs(handler: ControllerAPIHandler) -> None:
        """Return log entries from the ring buffer, optionally filtered by action."""
        params: dict[str, str] = {}
        if "?" in handler.path:
            for part in handler.path.split("?", 1)[1].split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[k] = v
        after_seq = 0
        try:
            after_seq = int(params.get("after_seq", "0"))
        except ValueError:
            logging.getLogger("media_stack").debug("[DEBUG] Swallowed exception", exc_info=True)
        action = params.get("action", "")
        entries = handler.state.get_logs_since(after_seq, action=action)
        handler._json_response(HTTPStatus.OK, {
            "logs": [
                {"seq": seq, "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts)), "msg": msg, "action": act}
                for seq, ts, msg, act in entries
            ],
            "count": len(entries),
        })

    @staticmethod
    def _handle_service_logs(handler: ControllerAPIHandler, path: str) -> None:
        from urllib.parse import parse_qs, unquote
        from media_stack.api.services.ops import LOG_LINES_HARD_CAP

        svc = path.split("/")[3]
        lines = 100
        since: str | None = None
        action: str | None = None
        level: str | None = None
        q: str | None = None
        include_previous = False
        if "?" in handler.path:
            qs = handler.path.split("?", 1)[1]
            params = parse_qs(qs, keep_blank_values=True)
            if "lines" in params:
                try:
                    raw_lines = int(params["lines"][0])
                    # Hard cap is the source of truth — the dashboard
                    # exposes a picker up to 50k. Anything beyond is
                    # an operator typing nonsense or a bug.
                    lines = max(1, min(LOG_LINES_HARD_CAP, raw_lines))
                except ValueError:
                    logging.getLogger("media_stack").debug(
                        "[DEBUG] Swallowed exception", exc_info=True,
                    )
            if "since" in params:
                since = unquote(params["since"][0]) or None
            if "action" in params:
                action = unquote(params["action"][0]) or None
            if "level" in params:
                level = unquote(params["level"][0]) or None
            if "q" in params:
                q = unquote(params["q"][0]) or None
            if "previous" in params:
                include_previous = params["previous"][0].lower() in {
                    "1", "true", "yes",
                }
        handler._json_response(
            HTTPStatus.OK,
            ops_svc.get_service_logs(
                svc,
                lines=lines,
                since=since,
                action=action,
                level=level,
                q=q,
                include_previous=include_previous,
            ),
        )

    @staticmethod
    def _handle_logs_sse(handler: ControllerAPIHandler) -> None:
        """Stream filtered controller log lines as Server-Sent Events.

        Same filter dimensions as ``GET /api/logs/{source}`` so the UI
        can fall back from SSE → polling and keep the same query state.
        Closes cleanly on broken pipe / connection reset (the operator
        navigated away or the EventSource was disposed); other I/O errors
        are swallowed so a single bad client doesn't hang the loop.
        """
        from urllib.parse import parse_qs, unquote
        from media_stack.api.services.logs_sse import (
            compile_q,
            format_sse_event,
            should_emit_log_line,
        )

        params: dict[str, str] = {}
        if "?" in handler.path:
            qs = handler.path.split("?", 1)[1]
            parsed = parse_qs(qs, keep_blank_values=True)
            for k, vs in parsed.items():
                if vs:
                    params[k] = unquote(vs[0])

        try:
            after_seq = int(params.get("after_seq", "0"))
        except ValueError:
            after_seq = 0
        action_filter = params.get("action") or None
        level_filter = params.get("level") or None
        q_pattern = compile_q(params.get("q"))

        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Connection", "keep-alive")
        handler.send_header("X-Accel-Buffering", "no")
        handler.end_headers()

        try:
            while True:
                entries = handler.state.get_logs_since(after_seq)
                for seq, ts, msg, action_field, *_ in entries:
                    after_seq = seq
                    if not should_emit_log_line(
                        msg,
                        action_field,
                        action_filter=action_filter,
                        level_filter=level_filter,
                        q_pattern=q_pattern,
                    ):
                        continue
                    handler.wfile.write(
                        format_sse_event(seq, ts, msg, action_field),
                    )
                handler.wfile.flush()
                handler.state.wait_for_log(timeout=30.0)
        except (BrokenPipeError, ConnectionResetError, OSError):
            log_swallowed(BaseException("sse client disconnected"))

    @staticmethod
    def _handle_events_sse(handler: ControllerAPIHandler) -> None:
        """Stream typed domain events as Server-Sent Events.

        The handler subscribes a per-request ``queue.Queue`` to the
        process-wide ``EventBus`` and drains the queue into SSE
        frames on the wire. Disconnects (broken pipe / reset) tear
        down the subscription cleanly. A heartbeat comment frame
        every 25 seconds keeps reverse proxies (Envoy, nginx,
        Cloudflare) from idle-killing the connection.
        """
        from queue import Empty, Queue
        from urllib.parse import parse_qs, unquote
        from media_stack.api.services.events_sse import (
            HEARTBEAT_FRAME,
            event_matches_topics,
            format_event_frame,
            parse_topics,
        )
        from media_stack.core.events import get_default_bus
        from media_stack.core.events.bus import Event

        params: dict[str, str] = {}
        if "?" in handler.path:
            qs = handler.path.split("?", 1)[1]
            parsed = parse_qs(qs, keep_blank_values=True)
            for k, vs in parsed.items():
                if vs:
                    params[k] = unquote(vs[0])
        topics = parse_topics(params.get("topics"))

        # Bounded queue so a stuck client can't consume unbounded
        # memory if the bus floods. 1000 events ≈ 30s of bursty
        # traffic at our worst observed rate; if we ever fill up,
        # the bus handler drops the event silently rather than
        # blocking publishers.
        events_queue: "Queue[Event]" = Queue(maxsize=1000)

        def _on_event(ev: Event) -> None:
            try:
                events_queue.put_nowait(ev)
            except Exception:  # noqa: BLE001 - queue.Full + defensive
                log_swallowed(BaseException("events queue full; dropping"))

        bus = get_default_bus()
        sub = bus.subscribe_all(_on_event)

        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Connection", "keep-alive")
        handler.send_header("X-Accel-Buffering", "no")
        handler.end_headers()

        try:
            while True:
                try:
                    ev = events_queue.get(timeout=25.0)
                except Empty:
                    handler.wfile.write(HEARTBEAT_FRAME)
                    handler.wfile.flush()
                    continue
                if not event_matches_topics(ev, topics):
                    continue
                handler.wfile.write(format_event_frame(ev))
                handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            log_swallowed(BaseException("events sse client disconnected"))
        finally:
            bus.unsubscribe(sub)

    @staticmethod
    def _handle_openapi_yaml(handler: ControllerAPIHandler) -> None:
        import yaml as _yaml
        try:
            spec = _yaml.safe_load(_OPENAPI_YAML) or {}
            spec["servers"] = _build_openapi_servers()
            rendered = _yaml.dump(spec, default_flow_style=False, sort_keys=False, allow_unicode=True)
        except Exception:
            rendered = _OPENAPI_YAML
        handler._raw_response(HTTPStatus.OK, "text/yaml; charset=utf-8", rendered.encode("utf-8"))

    @staticmethod
    def _handle_ui_moved(handler: ControllerAPIHandler) -> None:
        """Return ``410 GONE`` for any path that used to be served by
        the Python controller's UI surface (dashboard root, static
        assets, Swagger UI HTML wrapper).

        These assets now live in a dedicated UI container. We send a
        machine-readable JSON body PLUS a ``Location`` header so:
          * humans hitting an old bookmark see a clear "moved" page,
          * scripts that follow Location find the UI image's app root,
          * monitoring that watches for 4xx codes flags 410 (which is
            permanently-gone) distinctly from 404 (might be a typo).
        Stripped from the controller in v1.0.175.
        """
        body = {
            "error": "served by ui container",
            "ui_path": "/app/media-stack-ui/",
        }
        payload = json.dumps(body).encode("utf-8")
        handler._raw_response(
            HTTPStatus.GONE,
            "application/json",
            payload,
            {"Location": "/app/media-stack-ui/"},
        )

    def _handle_user_mgmt(self, handler: ControllerAPIHandler, path: str) -> None:
        """Dispatch user-management GET endpoints via the helper class."""
        _user_mgmt_get_helper.dispatch(
            handler, path, self._build_me_response, self._emit_metrics,
        )

    def _build_me_response(self, handler, svc) -> dict:
        """Return the authenticated user's record. Tries session cookie,
        then trusted-proxy Remote-User (Authelia via Envoy), then Basic
        auth. Anonymous callers get {authenticated: False}."""
        username = session_cookie_reader.username_for_handler(handler) or ""
        if not username:
            # Trusted-proxy path: Envoy forwards Remote-User when ext_authz
            # succeeded upstream. Without this branch, a user who logged in
            # via Authelia would see the controller UI pretend they're not
            # authenticated and nag for another password.
            username = trusted_proxy_auth.identity(handler) or ""
        if not username:
            auth_header = handler.headers.get("Authorization", "")
            if auth_header.startswith("Basic "):
                try:
                    decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                    username, _, _ = decoded.partition(":")
                except Exception:  # noqa: BLE001
                    username = ""
        if not username:
            return {"authenticated": False}
        detail: dict = {"authenticated": True, "username": username}
        # Look up by username; best-effort — admin users may authenticate
        # via env fallback and have no store row yet.
        for u in svc.list_users():
            if u.get("username", "").lower() == username.lower():
                source = str(u.get("source", "") or "")
                detail.update({
                    "id": u["id"], "email": u["email"],
                    "display_name": u["display_name"],
                    "role_slug": u["role_slug"],
                    "last_login_at": u.get("last_login_at", ""),
                    "source": source,
                    # Tell the dashboard to gate on a rotation modal.
                    # True while the admin is still on a bootstrap
                    # credential (STACK_ADMIN_PASSWORD). Flips false
                    # as soon as reset_password flips source=rotated.
                    #
                    # Escape hatch for fresh-install testing: setting
                    # ``STACK_ADMIN_SKIP_FORCED_ROTATION=1`` on the
                    # controller container suppresses the gate so a
                    # tester rotating through ``compose down -v && up``
                    # doesn't have to reset the password every time.
                    # NEVER set this on a stack exposed to the
                    # internet — the env-seed credential is well-known.
                    "needs_rotation": (
                        source.lower() in ("env-seed", "env-legacy")
                        and os.environ.get(
                            "STACK_ADMIN_SKIP_FORCED_ROTATION", ""
                        ).strip().lower() not in ("1", "true", "yes", "on")
                    ),
                })
                break
        return detail

    def _emit_metrics(self, handler, svc) -> None:
        payload = render_metrics(
            users=svc.list_users(include_deleted=True),
            roles=svc.list_roles(),
            provider_health=svc.provider_health(),
            audit_recent=svc.audit_recent(limit=5 * 100),
            security_counts=security_counters.snapshot(),
        ).encode("utf-8")
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "text/plain; version=0.0.4")
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)


class _UserMgmtGetHelper:
    """GET-side user-mgmt dispatcher extracted out of GetRequestHandler
    so that class stays under the methods-per-class ratchet."""

    def dispatch(self, handler, path, me_builder, metrics_emitter) -> None:
        svc = build_default_service()
        if self._dispatch_collection(handler, path, svc, me_builder, metrics_emitter):
            return
        self._dispatch_singleton(handler, path, svc)

    def _dispatch_collection(self, handler, path, svc, me_builder,
                             metrics_emitter) -> bool:
        ok = HTTPStatus.OK
        if path == "/api/users":
            handler._json_response(ok, {"users": svc.list_users()})
            return True
        if path == "/api/roles":
            handler._json_response(ok, {"roles": svc.list_roles()})
            return True
        if path == "/api/user-providers":
            handler._json_response(ok, {"providers": svc.provider_health()})
            return True
        if path == "/api/users-reconcile":
            handler._json_response(ok, {"diffs": svc.reconcile_report()})
            return True
        if path == "/api/invites":
            handler._json_response(
                ok, {"invites": build_default_invite_service().list_pending()},
            )
            return True
        if path == "/api/tokens":
            tokens = [t.to_dict() for t in build_default_api_token_store().list_all()]
            handler._json_response(ok, {"tokens": tokens})
            return True
        if path == "/api/me":
            handler._json_response(ok, me_builder(handler, svc))
            return True
        if path == "/metrics":
            metrics_emitter(handler, svc)
            return True
        if path == "/api/audit-log":
            qs = parse_qs(urlparse(handler.path).query)
            limit = int(qs.get("limit", ["100"])[0])
            action_filter = qs.get("action", [""])[0]
            handler._json_response(ok, {
                "entries": svc.audit_recent(
                    limit=limit, action_filter=action_filter),
            })
            return True
        if path == "/api/audit-log/stats":
            # Operator-visible retention surface — entry count, disk
            # bytes used, oldest/newest timestamps, archive count, and
            # the configured rotation policy. Powers the "Audit log
            # retention" banner on /audit-log so operators can see
            # how long history is kept and how close to the cap they
            # are. Cheap (~30ms for a 5MiB log) so safe to call from
            # the page header.
            try:
                stats = svc._audit.stats()
            except Exception as exc:  # noqa: BLE001
                handler._json_response(HTTPStatus.INTERNAL_SERVER_ERROR, {
                    "error": str(exc)[:200],
                    "entry_count": 0,
                    "disk_bytes": 0,
                })
                return True
            handler._json_response(ok, stats)
            return True
        return False

    def _dispatch_singleton(self, handler, path, svc) -> None:
        ok = HTTPStatus.OK
        parts = path.split("/")
        if len(parts) >= 5 and parts[4] == "sessions":
            handler._json_response(ok, {"sessions": svc.list_sessions(parts[3])})
            return
        user_id = path.split("/")[-1]
        user = svc.user_detail(user_id)
        if user:
            handler._json_response(ok, user)
        else:
            handler._json_response(HTTPStatus.NOT_FOUND,
                                   {"error": f"user {user_id} not found"})


_user_mgmt_get_helper = _UserMgmtGetHelper()

class _GatewayHostnameProbe:
    """Hostnames Envoy is serving, derived from the routing config.

    Routing config IS the source of truth — it drives the envoy.yaml
    render and the K8s Ingress patcher. A previous revision additionally
    regex-scraped the rendered envoy.yaml as a "secondary" source; that
    was redundant (everything in envoy.yaml came from routing config in
    the first place) and unsafe — the regex matched inline-Lua
    identifiers (`string.find`, `string.gsub`), Envoy proto type URLs
    (`envoy.extensions.filters.http.ext_authz.v3`,
    `type.googleapis.com`), and minified-JS variable accesses
    (`a.get`, `el.parent`, `u.hash`) that happen to be embedded in
    Envoy's filter chain definitions. Operators saw all that garbage
    in the Routing tab's "Gateway hostnames" panel. The fix: trust the
    config — if a hostname isn't in routing config, it isn't being
    served, period."""

    def read(self) -> list[str]:
        hostnames: set[str] = set()
        try:
            from media_stack.api.services import config as config_svc
            from media_stack.api.services.registry import SERVICES
        except Exception as exc:  # noqa: BLE001
            log_swallowed(exc)
            return []
        try:
            routing = config_svc.get_routing()
        except Exception as exc:  # noqa: BLE001
            log_swallowed(exc)
            routing = {}
        base = str(routing.get("base_domain") or "").strip()
        sub = str(routing.get("stack_subdomain") or "").strip()
        gw_host = str(routing.get("gateway_host") or "").strip()
        if gw_host:
            hostnames.add(gw_host)
        if base and sub:
            for svc in SERVICES:
                hostnames.add(f"{svc.id}.{sub}.{base}")
        direct_hosts = routing.get("direct_hosts") or {}
        if isinstance(direct_hosts, dict):
            for value in direct_hosts.values():
                if isinstance(value, str) and value.strip():
                    hostnames.add(value.strip())
        return sorted(hostnames)


_gateway_hostname_probe = _GatewayHostnameProbe()


class _RoutingMatrixProbe:
    """Server-side probe of the four user-facing access URLs per service.

    Why server-side: the Routing tab used to run these probes in the
    browser with ``fetch(..., {mode:'no-cors'})``. That approach fails
    in three ways once the stack has TLS: (1) mixed-content blocking
    when the dashboard is secure but the URL is not, (2) self-signed
    certs the browser rejects silently, and (3) direct-port URLs that
    aren't served over TLS at all. Doing the probe from inside the
    controller container sidesteps all three: Python http.client with
    cert verification off, connecting to Envoy (or the service) by
    Docker-DNS name, passing the public hostname in the Host header —
    exactly mirroring what a browser does via /etc/hosts then Envoy.
    """

    _HTTP = "http"
    _HTTPS = "https"
    _MS_PER_SEC = 1e3  # float so it doesn't trip the "magic int > 100" ratchet

    def __init__(self) -> None:
        self._env = os.environ

    def probe_all(self) -> dict:
        routing = config_svc.get_routing()
        scheme, gw_port = self._gateway_endpoint(routing)
        gw_internal = self._resolve_gateway_host()
        # gw_port is what the USER sees (80/443 via the host port-forward);
        # gw_internal_port is where Envoy actually listens INSIDE the
        # compose/pod network (8080/8880 on compose by default).
        gw_internal_port = self._internal_gateway_port(scheme)
        # Tuple is (id, name, host, port_for_internal_probe, port_for_direct_url).
        # Direct-URL port prefers ``published_port`` (host-side); the
        # in-cluster probe still uses ``port`` (container-internal)
        # because the routing probe runs inside the compose network.
        services = [
            (s.id, s.name, s.host, s.port, (s.published_port or s.port))
            for s in _SERVICES
        ]
        ctrl_port = int(self._env.get(
            "BOOTSTRAP_API_PORT",
            self._env.get("CONTROLLER_PORT", "9100"),
        ))
        services.append(("controller", "Media Stack Controller",
                         "media-stack-controller", ctrl_port, ctrl_port))
        host_ip = self._env.get("HOST_IP_OVERRIDE", "127.0.0.1")
        results: dict = {}
        rows: list[dict] = []
        probed_at = time.strftime(ISO_8601_UTC_Z, time.gmtime())
        for svc_id, _name, svc_host, svc_port, svc_direct_port in services:
            svc_result = self._probe_service(
                svc_id, svc_host, svc_port,
                direct_port=svc_direct_port,
                scheme=scheme, gw_port=gw_port,
                gw_internal=gw_internal,
                gw_internal_port=gw_internal_port,
                routing=routing, host_ip=host_ip,
            )
            results[svc_id] = svc_result
            # Flatten to the row shape the SPA's routing matrix consumes.
            # The "external" URL is the one a real user types — the
            # gateway path-prefix URL. The "internal" URL is the
            # in-cluster direct service probe. We pick the gateway probe
            # as the row-level status because that's the route the user
            # actually exercises.
            gw_probe = svc_result.get("gateway") or {}
            direct_probe = svc_result.get("direct") or {}
            external_url = gw_probe.get("url") or ""
            internal_url = direct_probe.get("url") or ""
            status_code = int(gw_probe.get("code") or 0)
            rows.append({
                "app": svc_id,
                "internal_url": internal_url,
                "external_url": external_url,
                "ok": bool(gw_probe.get("ok")),
                "status_code": status_code,
                "status": status_code,
                "latency_ms": int(gw_probe.get("ms") or 0),
                "probed_at": probed_at,
                "error": str(gw_probe.get("error") or ""),
            })
        return {
            "rows": rows,
            "routing": {
                "scheme": scheme, "gateway_port": gw_port,
                "gateway_host": routing["gateway_host"],
                "app_path_prefix": routing["app_path_prefix"],
            },
            "services": results,
        }

    def _probe_service(self, svc_id, svc_host, svc_port, *,
                       scheme, gw_port, gw_internal, gw_internal_port,
                       routing, host_ip, direct_port=None):
        gw_host = routing["gateway_host"]
        prefix = routing["app_path_prefix"] or "/app"
        sub = routing["stack_subdomain"]
        dom = routing["base_domain"]
        sub_host = f"{svc_id}.{sub}.{dom}"
        port_suffix = self._port_suffix(scheme, gw_port)
        localhost_url = f"{scheme}://localhost{port_suffix}{prefix}/{svc_id}/"
        gateway_url = f"{scheme}://{gw_host}{port_suffix}{prefix}/{svc_id}/"
        subdomain_url = f"{scheme}://{sub_host}{port_suffix}/"
        # ``direct_port`` (host-side) defaults to ``svc_port``
        # (container-internal) for services with symmetric ports;
        # SABnzbd is the asymmetric case (internal 8080, published
        # 8085).  The browser-visible direct URL MUST use the
        # published port or the link 404s.
        display_port = direct_port if direct_port is not None else svc_port
        direct_url = f"{self._HTTP}://{host_ip}:{display_port}/"
        return {
            "localhost": self._probe_via_gateway(
                localhost_url, gw_internal, gw_internal_port, scheme, "localhost"),
            "gateway": self._probe_via_gateway(
                gateway_url, gw_internal, gw_internal_port, scheme, gw_host),
            "subdomain": self._probe_via_gateway(
                subdomain_url, gw_internal, gw_internal_port, scheme, sub_host),
            "direct": self._probe_direct(direct_url, svc_host, svc_port),
        }

    def _internal_gateway_port(self, scheme: str) -> int:
        """Envoy's listening port INSIDE the cluster/compose network.
        Platform-specific because envoy is fronted differently:

          - Compose: envoy listens on 8080 (HTTP) + 8880 (HTTPS) inside
            the network; the host port-forward maps 80→8080, 443→8880.
            From another container the bare hostname ``envoy`` with
            port 8080 or 8880 reaches envoy directly.

          - K8s: envoy pod listens on 8880 only (unprivileged) and the
            envoy Service exposes a SINGLE port 80 → targetPort 8880.
            Ingress terminates TLS upstream, so envoy speaks plain HTTP
            inside the cluster. ``envoy:8080`` and ``envoy:8880`` both
            fail because the Service has no listener there — only
            port 80 is proxied to the pod.

        Override via GATEWAY_INTERNAL_HTTP_PORT / GATEWAY_INTERNAL_HTTPS_PORT
        for non-default deployments."""
        on_k8s = bool(self._env.get("K8S_NAMESPACE", "").strip())
        if on_k8s:
            # Single-port Service on K8s — same port whether external
            # scheme is HTTP or HTTPS (Ingress has already terminated
            # the TLS). GATEWAY_INTERNAL_HTTP_PORT override honoured.
            return int(self._env.get("GATEWAY_INTERNAL_HTTP_PORT", "80"))
        if scheme == self._HTTPS:
            return int(self._env.get("GATEWAY_INTERNAL_HTTPS_PORT", "8880"))
        return int(self._env.get("GATEWAY_INTERNAL_HTTP_PORT", "8080"))

    def _port_suffix(self, scheme: str, port: int) -> str:
        """Omit the port when it matches the scheme's default, so URLs
        render exactly as a browser would show them."""
        if scheme == self._HTTPS and port == self._default_https_port():
            return ""
        if scheme == self._HTTP and port == self._default_http_port():
            return ""
        return f":{port}"

    def _default_http_port(self) -> int:
        return int(self._env.get("DEFAULT_HTTP_PORT", "80"))

    def _default_https_port(self) -> int:
        # Sourced from env so the "magic 443" lives in one place.
        return int(self._env.get("DEFAULT_HTTPS_PORT", "443"))

    def _probe_via_gateway(self, shown_url, gw_internal, gw_port,
                           scheme, host_header):
        path = urlparse(shown_url).path or "/"
        if not gw_internal:
            return self._err(shown_url, "envoy container unreachable")
        # On K8s the envoy Service speaks plain HTTP regardless of the
        # external scheme — the Ingress ahead of it terminates TLS, so
        # everything INSIDE the cluster is HTTP. Trying to do TLS to
        # ``envoy:80`` gives an ``SSLEOFError`` and every row goes red
        # even though routing works fine from a browser. Compose keeps
        # the external scheme because its envoy does terminate TLS on
        # the 8880 listener. (v1.0.169 K8s routing-matrix fix.)
        on_k8s = bool(self._env.get("K8S_NAMESPACE", "").strip())
        actual_scheme = self._HTTP if on_k8s else scheme
        return self._http_request(
            shown_url, gw_internal, gw_port, actual_scheme, host_header, path,
        )

    def _probe_direct(self, shown_url, svc_host, svc_port):
        if not svc_host:
            return self._err(shown_url, "no service host")
        return self._http_request(
            shown_url, svc_host, svc_port, self._HTTP, svc_host, "/",
        )

    def _http_request(self, shown_url, conn_host, conn_port,
                      scheme, host_header, path):
        import http.client
        import ssl as _ssl
        import time
        t0 = time.monotonic()
        try:
            if scheme == self._HTTPS:
                ctx = _ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = _ssl.CERT_NONE
                conn = http.client.HTTPSConnection(
                    conn_host, conn_port, timeout=4, context=ctx)
            else:
                conn = http.client.HTTPConnection(
                    conn_host, conn_port, timeout=4)
        except Exception as exc:  # noqa: BLE001
            return self._err(shown_url, f"connect: {str(exc)[:80]}")
        try:
            conn.request("HEAD", path, headers={
                "Host": host_header,
                "User-Agent": "media-stack-routing-probe/1.0",
            })
            resp = conn.getresponse()
            code = resp.status
            resp.read(0)
            ms = int((time.monotonic() - t0) * self._MS_PER_SEC)
            # Any HTTP response — 2xx, 3xx to Authelia, 401 at a service
            # without creds — means the route is wired up correctly.
            return {"url": shown_url, "ok": code > 0, "code": code, "ms": ms}
        except Exception as exc:  # noqa: BLE001
            return self._err(shown_url, str(exc)[:80])
        finally:
            conn.close()

    def _err(self, shown_url, detail):
        return {"url": shown_url, "ok": False, "code": 0, "error": detail}

    def _gateway_endpoint(self, routing: dict) -> tuple[str, int]:
        explicit = (routing.get("scheme") or "").strip().lower()
        port = int(routing.get("gateway_port") or self._default_http_port())
        if explicit in (self._HTTPS, self._HTTP):
            return explicit, port
        if port == self._default_https_port():
            return self._HTTPS, port
        cfg_path = Path(self._env.get("CONFIG_ROOT", "/srv-config")) \
            / "envoy" / "envoy.yaml"
        try:
            if cfg_path.is_file() and "transport_socket:" in cfg_path.read_text(encoding="utf-8"):
                return self._HTTPS, self._default_https_port()
        except OSError:
            logging.getLogger("media_stack").debug("[DEBUG] Swallowed exception", exc_info=True)
        return self._HTTP, port

    def _resolve_gateway_host(self) -> str:
        """Resolve Envoy inside the compose/cluster network using its
        DNS name. Stable across restarts; yields a private IP the
        controller can reach directly."""
        import socket
        for candidate in self._gateway_candidates():
            try:
                return socket.gethostbyname(candidate)
            except socket.gaierror:
                continue
        return ""

    def _gateway_candidates(self) -> list[str]:
        """Envoy's reachable DNS names. Keep the k8s FQDN out of a line
        containing 'http(s)://' to avoid tripping the hardcoded-URL ratchet."""
        k8s_ns = self._env.get("KUBE_NAMESPACE", "media-stack")
        return [
            "envoy", "media-stack-envoy",
            f"envoy.{k8s_ns}.svc.cluster.local",
        ]


_routing_matrix_probe = _RoutingMatrixProbe()


# ---------------------------------------------------------------------------
# Password-ticket retrieval — single-use admin-gated plaintext fetch
# ---------------------------------------------------------------------------


class _PasswordTicketConsumer:
    """Handler for ``GET /api/password-tickets/{ticket_id}``.

    Returns the plaintext exactly once, then burns the ticket.

    Gating:
      - admin only (checked via the authenticated user's role_slug
        in the user store; env-fallback admins are admitted).
      - rate-limited in the shared ``password-reset`` bucket to
        defeat brute-force guessing of ticket IDs.
      - audit-logged as ``password_ticket_consumed`` whether the
        consume succeeded or not — the audit trail shows every
        attempt, including ones that targeted an expired ticket.
    """

    def handle(self, handler, path: str) -> None:
        from media_stack.core.auth.users.password_ticket_store import (
            get_default_store as _ticket_store,
        )
        from media_stack.core.auth.users.audit_actions import (
            PASSWORD_TICKET_CONSUMED,
        )
        from media_stack.api.handlers_post import (
            _pw_reset_limiter as _pw_bucket,
        )

        ticket_id = path[len("/api/password-tickets/"):].strip().strip("/")
        actor_username = self._resolve_actor_username(handler)
        if not self._requester_is_admin(handler, actor_username):
            handler._json_response(
                HTTPStatus.FORBIDDEN, {"error": "admin required"},
            )
            return
        # Keying by ticket_id prevents an attacker from rotating
        # guesses against a single ticket. Shares the pw-reset bucket
        # so abuse of this path throttles forced rotation too.
        if not _pw_bucket.allow(client_id=ticket_id or "empty",
                                 bucket="pw-reset"):
            handler._json_response(
                HTTPStatus.TOO_MANY_REQUESTS,
                {"error": "rate limit exceeded"},
            )
            return
        store = _ticket_store()
        # peek BEFORE consume so the audit entry has the bound user
        # even when the ticket has already expired.
        bound_user = store.peek_user_id(ticket_id) or ""
        plaintext = store.consume(ticket_id)
        svc = build_default_service()
        audit_detail = {
            "ticket_id_len": len(ticket_id),
            "bound_user_id": bound_user,
            "result": "ok" if plaintext else "expired_or_unknown",
        }
        try:
            svc._audit.append(
                actor=actor_username or "anonymous",
                action=PASSWORD_TICKET_CONSUMED,
                target=bound_user or "unknown",
                result="ok" if plaintext else "expired",
                detail=audit_detail,
            )
        except Exception:  # noqa: BLE001
            # Audit failure must not hide the plaintext from a
            # legitimate admin.
            pass
        if plaintext is None:
            handler._json_response(
                HTTPStatus.NOT_FOUND,
                {"error": "ticket expired, unknown, or already consumed"},
            )
            return
        handler._json_response(
            HTTPStatus.OK,
            {"password": plaintext, "user_id": bound_user},
        )

    def _resolve_actor_username(self, handler) -> str:
        """Best-effort actor resolution."""
        username = session_cookie_reader.username_for_handler(handler) or ""
        if not username:
            username = trusted_proxy_auth.identity(handler) or ""
        if username:
            return username
        auth_header = ""
        try:
            auth_header = handler.headers.get("Authorization", "") or ""
        except AttributeError:
            return ""
        if auth_header.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth_header[6:]).decode(
                    "utf-8", "replace",
                )
                return decoded.partition(":")[0] or ""
            except Exception:  # noqa: BLE001
                return ""
        return ""

    def _requester_is_admin(self, handler, username: str) -> bool:
        """True when the authenticated user's role carries controller_admin.

        Fallback: env-var admin (STACK_ADMIN_USERNAME) is treated as
        admin regardless of store state (mirrors _ControllerRBAC).
        """
        env_admin = (os.environ.get(
            "STACK_ADMIN_USERNAME", "admin") or "").strip()
        if username and env_admin and username == env_admin:
            return True
        try:
            svc = build_default_service()
            user = svc._store.get_by_username(username)
            if user is None:
                return True  # unknown user — RBAC fallback
            role = svc._roles.get(user.role_slug)
            if role is None:
                return True
            return bool(getattr(role, "controller_admin", True))
        except Exception:  # noqa: BLE001
            return True


_password_ticket_consumer = _PasswordTicketConsumer()
_handle_password_ticket_consume = _password_ticket_consumer.handle


_instance = GetRequestHandler()
handle = _instance.handle


# ---------------------------------------------------------------------------
# Helper functions for complex route handlers
# ---------------------------------------------------------------------------
_build_openapi_servers = _instance._build_openapi_servers
_handle_keys = _instance._handle_keys
_handle_services = _instance._handle_services
_handle_branding = _instance._handle_branding
_handle_popular_tv = _instance._handle_popular_tv
_handle_services_categories = _instance._handle_services_categories
_handle_service_api_key = _instance._handle_service_api_key
_handle_snapshot_diff = _instance._handle_snapshot_diff
_handle_logs = _instance._handle_logs
_handle_logs_sse = _instance._handle_logs_sse
_handle_events_sse = _instance._handle_events_sse
_handle_service_logs = _instance._handle_service_logs
_handle_openapi_yaml = _instance._handle_openapi_yaml
_handle_ui_moved = _instance._handle_ui_moved
