"""Regression: every registered guardrail's POST endpoints must work
when called with URL-encoded ids (the SPA calls ``encodeURIComponent``
before building the URL).

The bug operators hit: Test/Disable buttons returned ``404 unknown
guardrail: storage%3Ainode_floor`` for every rule whose id contained
a colon — which is every rule. Cause: the dispatcher in
``handlers_post.py`` extracted the id from ``handler.path`` and
looked it up in the registry without ``urllib.parse.unquote``-ing
first. ``encodeURIComponent("storage:inode_floor")`` →
``"storage%3Ainode_floor"``; registry lookup of the encoded form
misses, the dispatcher returns 404, and every operator-facing button
silently fails.

This test asserts the dispatcher unquotes path components. It does
NOT test every rule individually — it tests the encoding contract
once at the dispatcher boundary.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))


class GuardrailUrlDecodeRegressionTest(unittest.TestCase):
    def test_dispatcher_uses_unquote_on_rule_id(self) -> None:
        """Static check: the guardrails POST dispatcher reads the
        rule id from ``handler.path`` and MUST pass it through
        ``urllib.parse.unquote`` before calling ``registry.get``.

        Without this, every SPA-triggered Test / Disable / Save
        click 404s because the registry never contains the
        URL-encoded id ``storage%3Ainode_floor`` (only the literal
        ``storage:inode_floor``).

        We use a source-grep ratchet rather than a live HTTP fixture
        because the dispatcher is awkward to instantiate in
        isolation; a regression here would re-introduce the bug
        symmetrically for every rule.
        """
        src = (
            ROOT / "src" / "media_stack" / "api" / "handlers_post.py"
        ).read_text(encoding="utf-8")
        # Find the guardrails POST branch (matches the start of the
        # block — narrow window so we don't accidentally accept an
        # unquote() in some other path).
        anchor = 'if handler.path.startswith("/api/guardrails/"):'
        idx = src.find(anchor)
        self.assertNotEqual(idx, -1, "guardrails POST branch missing")
        # Look at the next ~600 chars of source.
        block = src[idx:idx + 1200]
        self.assertIn(
            "unquote",
            block,
            "guardrails POST branch must call urllib.parse.unquote on "
            "the rule id extracted from handler.path — without it, "
            "every SPA-triggered Test/Disable click 404s on rules "
            "whose ids contain reserved chars (every rule has a colon).",
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
