"""Ratchet: the dashboard's PWA service worker must NOT hijack
navigations to sister apps under ``/app/<other-service>/``.

Background
----------
The dashboard SPA is a PWA. Its service worker registers a
``navigateFallback`` of ``/index.html`` so deep-link routes inside
the dashboard work offline (``/app/media-stack-ui/me``,
``/app/media-stack-ui/ops``, etc. all serve the same app shell).
Without scoping, that fallback applies to ANY same-origin navigation:
when the operator visits ``/app/homepage/`` (gethomepage), ``/app/sonarr/``
or any other sister app behind the edge gateway, the SW silently
substitutes the dashboard's ``index.html``. The browser then loads the
*dashboard* SPA at the wrong mount; the Lua prefix patcher rewrites
every API call to ``/app/<other-service>/api/...`` and the page is
useless (70+ 404s, no working UI).

Operator's words: "how do I get to other apps now?"

The fix is a ``navigateFallbackDenylist`` rule that excludes
``/app/<id>/`` for every id that ISN'T the dashboard's own mount.
This ratchet asserts the rule is present.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
# Vite's PWA strategy switched from ``generateSW`` (workbox config
# inline in vite.config.ts) to ``injectManifest`` — the SW source is
# owned by the project at ``ui/src/sw.ts`` and the denylist defaults
# live next to it in ``ui/src/sw-config.ts``. The runtime denylist is
# pulled from ``GET /sw-config.json`` at install time, but the
# baked-in defaults still need to assert the cross-app safety
# invariant for offline / first-install scenarios.
SW_SOURCE = REPO_ROOT / "ui" / "src" / "sw.ts"
SW_CONFIG = REPO_ROOT / "ui" / "src" / "sw-config.ts"

# What we want to see in the denylist. Must exclude /api/* AND
# every /app/* path that isn't the dashboard.
RE_API_DENY = re.compile(r"\\?\^?\/api\\?\/")
RE_CROSS_APP_DENY = re.compile(
    r"\\?\^?\/app\\?\/.*media-stack-ui",
    re.IGNORECASE,
)


def _read_sw_source() -> str:
    return SW_SOURCE.read_text(encoding="utf-8")


def _read_sw_config() -> str:
    return SW_CONFIG.read_text(encoding="utf-8")


def test_navigate_fallback_denylist_excludes_api() -> None:
    sw = _read_sw_source()
    cfg = _read_sw_config()
    assert "navigationHandler" in sw or "createHandlerBoundToURL" in sw, (
        "ui/src/sw.ts must register a navigation handler for deep-"
        "link routes inside the dashboard SPA."
    )
    assert RE_API_DENY.search(cfg), (
        "navigateFallbackDenylist (in ui/src/sw-config.ts defaults) "
        "must exclude ``/api/*`` so the SW doesn't substitute the "
        "SPA shell for JSON API responses."
    )


def test_navigate_fallback_denylist_excludes_cross_app() -> None:
    """The denylist must contain a /app/<not-media-stack-ui>/ rule."""
    cfg = _read_sw_config()
    assert RE_CROSS_APP_DENY.search(cfg), (
        "navigateFallbackDenylist (defaults in ui/src/sw-config.ts) "
        "must exclude ``/app/<service>/`` paths for every service "
        "that ISN'T media-stack-ui. Without this, the dashboard's "
        "PWA service worker hijacks navigations to sister apps "
        "(homepage, sonarr, jellyfin, qbittorrent, etc.) and serves "
        "its own index.html — operators land on a broken page with "
        "the wrong app loaded. Required regex form: "
        "``/^\\/app\\/(?!media-stack-ui(?:\\/|$))/`` or equivalent. "
        "See the ratchet docstring for the original incident."
    )


def test_navigate_fallback_target_is_index_html() -> None:
    """Make sure the fallback still points at ``/index.html`` — if
    someone replaces it with a different file the cross-app fix
    above might mask a different break."""
    sw = _read_sw_source()
    assert 'createHandlerBoundToURL("/index.html")' in sw, (
        "navigation handler must bind to ``/index.html`` (the SPA "
        "shell). Changing it without updating this ratchet hides "
        "regressions in offline routing."
    )
