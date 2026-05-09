"""Service-worker config endpoint.

The dashboard's PWA service worker needs to know two things at
runtime:

  1. **Its own basepath** — where the dashboard SPA is mounted
     (``/app/media-stack-ui/`` by default; operators can rename it
     via the routing-admin page). The SW intercepts navigations
     under this path and serves ``index.html`` for offline routing.

  2. **The other ``/app/<service>/`` mount points** — every
     deployed sister app the SW should NOT hijack. When the operator
     visits ``/app/sonarr/``, the SW must let the network handle it
     so Envoy routes to the Sonarr container.

Hardcoding either of these in ``vite.config.ts`` couples build to
deployment shape — a custom-prefixed deploy needs a rebuild. By
emitting both as a JSON document the SW fetches at install/update,
the routing engine becomes the single source of truth.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("controller_api")

from media_stack.services.apps.stack.routing_defaults import (
    DASHBOARD_SERVICE_ID,
)


def get_sw_config() -> dict[str, Any]:
    """Build the service-worker configuration payload.

    Shape::

        {
          "version": 1,
          "basepath": "/app/media-stack-ui",
          "denylist_patterns": [
            "^/api/",
            "^/app/(?!media-stack-ui(?:/|$))"
          ],
          "allowed_app_prefixes": ["/app/media-stack-ui"],
          "sister_app_prefixes": ["/app/sonarr", "/app/jellyfin", ...]
        }

    ``denylist_patterns`` is what the SW plugs straight into
    ``navigationRoute`` matching — pre-compiled regex strings so the
    SW doesn't re-derive them from the prefix lists.
    ``allowed_app_prefixes`` and ``sister_app_prefixes`` are also
    surfaced so future SW logic (e.g. background sync per app) can
    use them without re-parsing the patterns.

    Falls back to safe defaults when the registry can't be loaded —
    a misconfigured controller still serves a usable SW config
    rather than an empty payload.
    """
    basepath = _resolve_dashboard_basepath()
    sister_prefixes = _list_sister_app_prefixes(basepath=basepath)
    denylist_patterns = _build_denylist_patterns(
        basepath=basepath,
        sister_prefixes=sister_prefixes,
    )
    return {
        "version": 1,
        "basepath": basepath,
        "denylist_patterns": denylist_patterns,
        "allowed_app_prefixes": [basepath],
        "sister_app_prefixes": sister_prefixes,
    }


def _resolve_dashboard_basepath() -> str:
    """Return the dashboard's mount point, e.g.
    ``"/app/media-stack-ui"`` (no trailing slash).

    Resolution order:

      1. ``DASHBOARD_BASEPATH_OVERRIDE`` env — escape hatch for
         non-default deploys.
      2. Profile's ``routing.app_path_prefix`` + the dashboard
         service id — the canonical source of truth.
      3. Hardcoded default ``/app/media-stack-ui`` — only hit when
         the profile is unreachable AND the env override isn't set.
    """
    override = os.environ.get("DASHBOARD_BASEPATH_OVERRIDE", "").strip()
    if override:
        return _normalize_path(override)

    app_prefix = _read_app_path_prefix_from_profile()
    return _normalize_path(f"{app_prefix}/{DASHBOARD_SERVICE_ID}")


def _read_app_path_prefix_from_profile() -> str:
    """Best-effort read of ``routing.app_path_prefix`` from the
    bootstrap profile YAML. Returns ``"/app"`` on any error so a
    misconfigured profile doesn't break the SW config endpoint."""
    try:
        # Imported lazily to avoid a circular import via
        # ``api.services.config`` during module load.
        from media_stack.api.services.config._routing import (
            get_routing as _get_routing,
        )
        cfg = _get_routing()
        prefix = (cfg or {}).get("app_path_prefix")
        if isinstance(prefix, str) and prefix.strip():
            return prefix.strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[sw_config] falling back to default /app prefix: %s", exc,
        )
    return "/app"


def _list_sister_app_prefixes(*, basepath: str) -> list[str]:
    """Enumerate ``/app/<service>`` prefixes for every registered
    service that ISN'T the dashboard. Used by the SW to recognize
    paths it should pass through to the network (Envoy)."""
    try:
        from media_stack.core.service_registry.registry import SERVICES
    except Exception as exc:  # noqa: BLE001
        logger.debug("[sw_config] registry unavailable: %s", exc)
        return []
    app_prefix = _read_app_path_prefix_from_profile()
    out: list[str] = []
    for s in SERVICES:
        sid = getattr(s, "id", "")
        if not sid or sid == DASHBOARD_SERVICE_ID:
            continue
        prefix = _normalize_path(f"{app_prefix}/{sid}")
        if prefix != basepath:
            out.append(prefix)
    out.sort()
    return out


def _build_denylist_patterns(
    *,
    basepath: str,
    sister_prefixes: list[str],
) -> list[str]:
    """Assemble the regex strings the SW navigates against. Two
    patterns by default:

      * ``^/api/`` — controller REST surface; SW should never
        substitute the SPA shell for a JSON 401/404.
      * ``^/app/(?!<dashboard-segment>(?:/|$))`` — sister-app
        passthrough.

    The second pattern is built from the basepath segments rather
    than hard-coded so a renamed dashboard mount automatically
    reshapes the regex.
    """
    patterns: list[str] = [r"^/api/"]
    # The basepath is something like ``/app/media-stack-ui``; we
    # need the segment that follows ``/app/``.
    parts = basepath.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "app":
        dashboard_segment = re.escape(parts[1])
        patterns.append(
            rf"^/app/(?!{dashboard_segment}(?:/|$))",
        )
    elif len(parts) == 1:
        # Non-prefixed deploy (everything at root). SW only needs
        # the /api/ exclusion in this case.
        pass
    return patterns


def _normalize_path(p: str) -> str:
    """Trim trailing slashes; collapse repeated slashes; ensure
    leading slash."""
    if not p:
        return ""
    cleaned = "/" + "/".join(seg for seg in p.split("/") if seg)
    return cleaned


# Lazily imported here so the regex helpers above can use it without
# pulling re into the module-load cost.
import re  # noqa: E402
