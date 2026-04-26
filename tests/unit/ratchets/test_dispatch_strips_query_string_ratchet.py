"""Ratchet: every POST/GET dispatcher that route-matches via
``path in _EXACT`` must strip the query string FIRST.

Why a ratchet
-------------
On 2026-04-24 the media-integrity ``POST /reconcile?dry_run=1``
silently 404'd because ``handlers_post.py`` did
``handlers.matches_post(handler.path)`` with the raw path. The
dispatcher's ``_POST_EXACT`` is an exact-string set, so the path
+ query never matches and the request falls through to a 404.

GET dispatch already strips the query at server.py:113
(``path = handler.path.split("?")[0]``). POST dispatch did not.

This ratchet enforces the invariant going forward: anywhere the
code does ``<x>.matches_post(...)`` or ``<x>.matches_get(...)``,
the argument MUST be a path with no ``?`` in it. We can't trivially
type-check this in Python, so we do an AST scan: any matches_post/
matches_get call whose argument is `handler.path` or `self.path`
without an obvious split, fails the test.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[3]
_API_DIR = _ROOT / "src" / "media_stack" / "api"


# Files that contain dispatch logic. If you add a new dispatcher
# module that uses ``matches_post``/``matches_get``, add it here so
# the ratchet runs against it.
_DISPATCHER_FILES = (
    _API_DIR / "handlers_get.py",
    _API_DIR / "handlers_post.py",
    _API_DIR / "server.py",
)


_MATCH_FUNCTIONS = ("matches_post", "matches_get", "matches")


class DispatchQueryStringRatchet(unittest.TestCase):

    def test_dispatcher_files_exist(self) -> None:
        for f in _DISPATCHER_FILES:
            self.assertTrue(f.is_file(), f"missing dispatcher file: {f}")

    def test_no_raw_handler_path_in_match_calls(self) -> None:
        """For each ``X.matches_*(path_arg)`` call, ``path_arg`` must
        NOT be the bare ``handler.path``/``self.path`` attribute.

        The fix is to compute a clean path first:

            clean = handler.path.split("?", 1)[0]
            if x.matches_post(clean):
                ...

        or equivalently use ``urlparse``. This test asserts the
        cleaning is present at every call site.
        """
        violations: list[str] = []
        for source_file in _DISPATCHER_FILES:
            tree = ast.parse(source_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                # Must be ``X.matches_post(...)`` form.
                if not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr not in _MATCH_FUNCTIONS:
                    continue
                if not node.args:
                    continue
                arg = node.args[0]
                if _is_raw_handler_path(arg):
                    violations.append(
                        f"{source_file.name}:{node.lineno}: "
                        f"matches call uses raw {ast.unparse(arg)} — "
                        f"this includes the query string and will "
                        f"miss any URL with `?...`. Strip the query "
                        f"first via ``.split('?', 1)[0]``."
                    )

        self.assertFalse(
            violations,
            msg=(
                "\n\nDispatch route-matching with a raw query-bearing "
                "path detected. This is the bug that broke "
                "POST /api/media-integrity/reconcile?dry_run=1 in "
                "v1.0.172.\n\n"
                + "\n".join(violations)
                + "\n"
            ),
        )

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


def _is_raw_handler_path(node: ast.AST) -> bool:
    """True if ``node`` is exactly ``handler.path`` or ``self.path``
    (no ``.split(...)``, no ``[...]`` subscript)."""
    if not isinstance(node, ast.Attribute):
        return False
    if node.attr != "path":
        return False
    if not isinstance(node.value, ast.Name):
        return False
    return node.value.id in ("handler", "self")


if __name__ == "__main__":
    unittest.main()
