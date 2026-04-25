"""Ratchet: every static-asset reference in shipped HTML/JS resolves
to a file the server actually serves.

Why a ratchet
-------------
On 2026-04-24 the Media-Integrity tab shipped with a script tag
pointing at ``/static/tab_media_integrity.js``. The Python
``ControllerAPIHandler`` only serves under ``/api/static/...``, so
the dynamic ``<script>`` injection 404'd silently and the tab
rendered as a blank white page. The bug was invisible to existing
unit tests because no test exercises the *content* of dashboard.html
against the *static-file dispatcher*.

This ratchet closes that hole. It scans every ``src=`` / ``href=``
in dashboard.html (and any inline-string equivalents like
``s.src='...'``) and asserts:

1. The path either starts with an external URL (``http(s)://``,
   ``data:``, ``mailto:``, ``//``) — in which case we don't care, OR
2. The path starts with ``/api/static/`` — the only static prefix
   the server registers, OR
3. The path is in an explicit allowlist (e.g. ``/`` for the root,
   API endpoints under ``/api/`` that don't need a file).

For case (2), the file MUST exist under ``src/media_stack/api/static/``.

A new tab JS file shipped without serving wiring fails this test.
A path-prefix typo (``/static/`` vs ``/api/static/``) fails this test.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]
_DASHBOARD = _ROOT / "src" / "media_stack" / "api" / "dashboard.html"
_STATIC_DIR = _ROOT / "src" / "media_stack" / "api" / "static"
_STATIC_PREFIX = "/api/static/"


# Patterns that point at a static asset:
# - ``src="..."`` / ``src='...'`` in HTML or JS string literals.
# - ``href="..."`` for stylesheets.
# - JavaScript ``s.src='...'`` (dynamic <script> injection).
_REFS = [
    re.compile(r'\bsrc\s*=\s*["\']([^"\']+)["\']'),
    re.compile(r'\bhref\s*=\s*["\']([^"\']+)["\']'),
    re.compile(r'\.\s*src\s*=\s*["\']([^"\']+)["\']'),
]


# Allowed external URI schemes. We ignore anything under these.
_EXTERNAL_PREFIXES = ("http://", "https://", "data:", "mailto:", "//", "#")


# Paths that are explicitly NOT static assets — they're API routes,
# or page-level routes the server handles directly. Maintained
# manually so adding a new endpoint is a deliberate review event.
_NON_STATIC_ALLOWLIST = {
    "/",
    "/dashboard",
    "/api/docs",
    "/api/openapi.yaml",
    "/api/openapi.json",
}


class StaticAssetPathRatchet(unittest.TestCase):

    def test_dashboard_html_exists(self) -> None:
        self.assertTrue(
            _DASHBOARD.is_file(),
            f"dashboard.html missing at {_DASHBOARD}",
        )

    def test_static_dir_exists(self) -> None:
        self.assertTrue(
            _STATIC_DIR.is_dir(),
            f"static dir missing at {_STATIC_DIR}",
        )

    def test_every_static_ref_resolves_to_a_served_file(self) -> None:
        """Every ``/api/static/...`` reference in dashboard.html
        points at a file that exists on disk and will therefore be
        served by ``_handle_static_asset``."""
        text = _DASHBOARD.read_text(encoding="utf-8")
        seen_static_paths: set[str] = set()
        for pattern in _REFS:
            for match in pattern.finditer(text):
                path = match.group(1).strip()
                if not path or _is_external(path):
                    continue
                if not path.startswith("/"):
                    # Relative path — out of scope for this ratchet.
                    continue
                if path in _NON_STATIC_ALLOWLIST or path.startswith("/api/") and not path.startswith("/api/static/"):
                    # Either an explicit allowlist entry or a
                    # non-static API route. Skip.
                    continue
                # Same-page query-string anchors (``/?tab=foo``) and
                # fragments are navigation, not static assets.
                if "?" in path or path.startswith("/?") or path == "/":
                    continue
                seen_static_paths.add(path)

        self.assertNotEqual(
            seen_static_paths,
            set(),
            "ratchet self-check: dashboard.html should reference at "
            "least one /api/static/ asset (Swagger UI, branding, tab "
            "JS). If this assertion fires the regex above is broken.",
        )

        for path in sorted(seen_static_paths):
            self.assertTrue(
                path.startswith(_STATIC_PREFIX),
                msg=(
                    f"\n\nStatic-asset reference {path!r} in dashboard.html "
                    f"does not begin with the canonical prefix "
                    f"{_STATIC_PREFIX!r}.\n\n"
                    f"The server only serves files under "
                    f"{_STATIC_PREFIX!r}; any other path will 404.\n\n"
                    f"Fix: change the reference to "
                    f"{_STATIC_PREFIX + path.lstrip('/')!r} (or add "
                    f"the path to ``_NON_STATIC_ALLOWLIST`` if it is "
                    f"genuinely not a static asset).\n"
                ),
            )
            filename = path[len(_STATIC_PREFIX):]
            target = _STATIC_DIR / filename
            self.assertTrue(
                target.is_file(),
                msg=(
                    f"\n\nStatic-asset reference {path!r} in "
                    f"dashboard.html resolves to {target} which does "
                    f"not exist.\n\n"
                    f"Fix: ship the file at "
                    f"src/media_stack/api/static/{filename}, or remove "
                    f"the reference if it was a typo.\n"
                ),
            )

    def test_no_bare_slash_static_references(self) -> None:
        """Hardline check: NO file in shipped HTML/JS may reference
        ``/static/...`` (without the ``/api/`` prefix). The earlier
        ratchet covers the broader case; this one fails LOUDLY for
        the specific anti-pattern that bit us in v1.0.172 → v1.0.173."""
        bad_pattern = re.compile(r'["\'/]/static/[A-Za-z0-9_\-./]+')
        for source in (
            _DASHBOARD,
            *(_STATIC_DIR.glob("*.js")),
        ):
            if not source.is_file():
                continue
            text = source.read_text(encoding="utf-8", errors="replace")
            hits = bad_pattern.findall(text)
            # Filter out the canonical /api/static/ references which
            # the regex above also matches as part of a longer path.
            real_hits = [h for h in hits if "/api/static/" not in h]
            self.assertFalse(
                real_hits,
                msg=(
                    f"\n\n{source.name} contains references to "
                    f"``/static/...`` (without the /api prefix). "
                    f"The server does NOT serve under /static; only "
                    f"/api/static/. Update each reference to "
                    f"/api/static/...\n\n"
                    f"Offending paths: {real_hits}\n"
                ),
            )


def _is_external(path: str) -> bool:
    return any(path.startswith(p) for p in _EXTERNAL_PREFIXES)


if __name__ == "__main__":
    unittest.main()
