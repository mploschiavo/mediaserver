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
VITE_CONFIG = REPO_ROOT / "ui" / "vite.config.ts"

# What we want to see in the denylist. Must exclude /api/* AND
# every /app/* path that isn't the dashboard.
RE_API_DENY = re.compile(r"\\?\^?\/api\\?\/")
RE_CROSS_APP_DENY = re.compile(
    r"\\?\^?\/app\\?\/.*media-stack-ui",
    re.IGNORECASE,
)


def _read_vite_config() -> str:
    return VITE_CONFIG.read_text(encoding="utf-8")


def test_navigate_fallback_denylist_excludes_api() -> None:
    text = _read_vite_config()
    assert "navigateFallback" in text, (
        "ui/vite.config.ts must register a PWA navigateFallback for "
        "deep-link routes inside the dashboard SPA."
    )
    assert RE_API_DENY.search(text), (
        "navigateFallbackDenylist must exclude ``/api/*`` so the SW "
        "doesn't substitute the SPA shell for JSON API responses."
    )


def test_navigate_fallback_denylist_excludes_cross_app() -> None:
    """The denylist must contain a /app/<not-media-stack-ui>/ rule."""
    text = _read_vite_config()
    assert RE_CROSS_APP_DENY.search(text), (
        "navigateFallbackDenylist must exclude ``/app/<service>/`` "
        "paths for every service that ISN'T media-stack-ui. Without "
        "this, the dashboard's PWA service worker hijacks navigations "
        "to sister apps (homepage, sonarr, jellyfin, qbittorrent, "
        "etc.) and serves its own index.html — operators land on a "
        "broken page with the wrong app loaded. Required regex form: "
        "``/^\\/app\\/(?!media-stack-ui(?:\\/|$))/`` or equivalent. "
        "See the ratchet docstring for the original incident."
    )


def test_navigate_fallback_target_is_index_html() -> None:
    """Make sure the fallback still points at ``/index.html`` — if
    someone replaces it with a different file the cross-app fix
    above might mask a different break."""
    text = _read_vite_config()
    assert 'navigateFallback: "/index.html"' in text, (
        "navigateFallback must point at ``/index.html`` (the SPA "
        "shell). Changing it without updating this ratchet hides "
        "regressions in offline routing."
    )
