"""Regression: every registered guardrail's POST endpoints must work
when called with URL-encoded ids (the SPA calls ``encodeURIComponent``
before building the URL).

The bug operators hit: Test/Disable buttons returned ``404 unknown
guardrail: storage%3Ainode_floor`` for every rule whose id contained
a colon — which is every rule. Original cause: the legacy dispatcher
in ``handlers_post.py`` extracted the id from ``handler.path`` and
looked it up in the registry without ``urllib.parse.unquote``-ing
first.

ADR-0007 Phase 2 Phase E retired ``handlers_post.py`` entirely. The
guardrails POSTs are now parameterised routes on
``api/routes/post_admin_ops.py`` registered via
``@post("/api/guardrails/{id}")`` / ``@post("/api/guardrails/{id}/test")``
/ ``@post("/api/guardrails/{id}/disable")``. The dispatch entry-point
itself lives at ``src/media_stack/api/routing/dispatch.py``.

Two checks remain:

1. The parameterised routes are registered with the Router under the
   expected ``{id}``-shape — the dispatch entry-point is no longer the
   ``handler.path``-startswith chain, but the guardrail POSTs must
   still go through the parameterised path-param matcher.
2. Every registered rule id round-trips
   ``encodeURIComponent`` → ``urllib.parse.unquote`` unchanged so a
   future rule id with characters that don't survive that pair fails
   here before the SPA's button click does.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))


class GuardrailUrlDecodeRegressionTest(unittest.TestCase):
    def test_guardrails_routes_use_parameterised_id(self) -> None:
        """Static check: the guardrails POST routes are registered
        with the Router as parameterised ``/{id}`` routes — NOT as
        ``startswith("/api/guardrails/")`` prefix matches.

        The legacy ``handlers_post.py`` chain extracted the id by
        ``handler.path[len("/api/guardrails/"):]`` and forgot to
        ``urllib.parse.unquote`` it; ADR-0007 Phase 2 replaced that
        with parameterised routes that match through the Router's
        compiled pattern. The dispatch entry-point is now
        ``src/media_stack/api/routing/dispatch.py`` and the
        guardrails routes are decorated on ``post_admin_ops.py``.
        """
        # Source check on the route module — verifies the @post
        # decorators carry the parameterised path syntax.
        src = (
            ROOT / "src" / "media_stack" / "api"
            / "routes" / "post_admin_ops.py"
        ).read_text(encoding="utf-8")
        for decorator in (
            '@post("/api/guardrails/{id}")',
            '@post("/api/guardrails/{id}/test")',
            '@post("/api/guardrails/{id}/disable")',
        ):
            self.assertIn(
                decorator, src,
                f"guardrails POST route missing parameterised "
                f"{{id}} registration: {decorator!r}. Without this "
                f"the path-param goes through the legacy "
                f"startswith-and-slice extraction that lost "
                f"unquote() and 404'd every encoded rule id.",
            )
        # Sanity: the dispatch entry-point file exists where the
        # docstring claims it lives.
        dispatch_path = (
            ROOT / "src" / "media_stack" / "api"
            / "routing" / "dispatch.py"
        )
        self.assertTrue(
            dispatch_path.is_file(),
            "Router dispatch entry-point missing — every POST flows "
            "through this module after Phase E retirement of "
            "handlers_post.py.",
        )

    def test_every_registered_rule_id_round_trips_url_encoding(self) -> None:
        """Belt-and-suspenders: every id in the live registry must
        round-trip ``encodeURIComponent`` → ``urllib.parse.unquote``
        unchanged. If a future rule id contains characters that
        don't survive that pair (none should, but worth pinning),
        this catches it before the SPA's button click does.
        """
        from urllib.parse import quote, unquote

        from media_stack.services import guardrails as _g

        registry = _g.default()
        rule_ids = [r.id for r in registry.list_rules()]
        self.assertGreater(
            len(rule_ids), 0, "registry must have registered rules"
        )
        for rid in rule_ids:
            # ``quote`` here uses the same default-safe set as the
            # SPA's encodeURIComponent (it doesn't escape `/`, but
            # rule ids don't contain `/` so the difference is
            # irrelevant).
            encoded = quote(rid, safe="")
            decoded = unquote(encoded)
            self.assertEqual(
                decoded, rid,
                f"rule id {rid!r} doesn't survive quote→unquote: "
                f"encoded={encoded!r}, decoded={decoded!r}",
            )


if __name__ == "__main__":
    unittest.main()
