"""Ratchet: the central dispatcher must strip the query string
BEFORE matching the path against route registrations.

History
-------
On 2026-04-24 the media-integrity ``POST /reconcile?dry_run=1``
silently 404'd because legacy ``handlers_post.py`` did
``handlers.matches_post(handler.path)`` with the raw path. The
elif-chain dispatcher's ``_POST_EXACT`` was an exact-string set,
so the path + query never matched and the request fell through
to a 404.

ADR-0007 Phase E retired the elif-chain ``handlers_get.py`` /
``handlers_post.py`` files entirely; routing is now Router-based
auto-discovery (``RouteModule.__init_subclass__``) with the
HTTP-method preflight + path normalization centralized in
``server.py``. The previous ``matches_post``/``matches_get`` AST
scan therefore no longer has any call sites to scan — but the
invariant still matters: if ``server.py`` ever stops stripping the
query before dispatch, every ``?dry_run=1``-style request will
miss the registered handler.

This file now asserts the central server module still derives
``path = self.path.split("?", 1)[0]`` (or equivalent) at the
points where it dispatches GET / sudo / POST requests.
"""

from __future__ import annotations

import unittest
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[3]
_API_DIR = _ROOT / "src" / "media_stack" / "api"


class DispatchQueryStringRatchet(unittest.TestCase):

    def test_server_module_exists(self) -> None:
        """``server.py`` is the central preflight + dispatch entry
        point. ADR-0007 Phase E removed handlers_get/post.py — server.py
        is the only remaining dispatcher file, and the route modules
        under ``api/routes/`` register themselves via
        ``RouteModule.__init_subclass__``."""
        server = _API_DIR / "server.py"
        self.assertTrue(server.is_file(), f"missing server file: {server}")

    def test_get_dispatcher_strips_query_globally(self) -> None:
        """Sanity check: ``server.py`` should still derive ``path =
        self.path.split("?")[0]`` for the GET preflight / auth path.
        Without this, the global path normalization the GET tree
        depends on disappears."""
        text = (_API_DIR / "server.py").read_text(encoding="utf-8")
        self.assertIn(
            'self.path.split("?")[0]',
            text,
            "server.py no longer normalizes ``self.path`` for the "
            "auth/dispatch path. Restore the split or update this "
            "ratchet with the new mechanism.",
        )

    def test_post_dispatcher_strips_query(self) -> None:
        """server.py should strip the query before POST dispatch too.
        ADR-0007 Phase E: the POST elif chain in handlers_post.py was
        replaced by Router auto-discovery, but the preflight in
        server.py is responsible for handing the route module a clean
        path. Without this, the original v1.0.172 bug — POST
        ``/reconcile?dry_run=1`` 404-ing because the route key is the
        full ``/reconcile?dry_run=1`` string — recurs.
        """
        text = (_API_DIR / "server.py").read_text(encoding="utf-8")
        self.assertIn(
            'self.path.split("?", 1)[0]',
            text,
            "server.py no longer strips the query string before POST "
            "dispatch. Restore the split or update this ratchet.",
        )


if __name__ == "__main__":
    unittest.main()
