"""Tests for the contract-declared ``plugin.job_aliases`` system.

Drove the addition: the user pointed out that aliases are job
metadata, not dispatch code. The first cut had

    if action_name == "reconcile":
        action_name = "bootstrap"

hardcoded in ``_dispatch_action``. The fix: every contract can
declare a ``plugin.job_aliases: { alias: canonical }`` map. The
discovery code merges all maps; ``resolve_alias`` looks the alias
up; ``run_job`` calls the resolver before tree lookup. Result:
adding a new alias is a YAML edit, the dispatch stays one line,
and the ratchet stays empty.

Coverage:

1. ``discover_job_aliases`` reads ``plugin.job_aliases`` from
   every contract and merges them (first-write-wins on
   collisions).
2. ``resolve_alias`` returns the canonical name for an alias and
   passes through unknown names unchanged.
3. ``resolve_alias`` is cycle-safe — a contract that maps
   ``a -> b`` and ``b -> a`` doesn't loop forever.
4. ``run_job(alias_name)`` runs the canonical job (the
   end-to-end smoke test the user actually cares about).
5. ``KNOWN_ACTIONS`` includes alias names so
   ``POST /actions/reconcile`` returns 202, not 404.
6. The shipped contract aliases the user-visible ``reconcile``
   to canonical ``bootstrap`` (regression pin)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))


# Force a fresh discovery for each test class so we don't leak
# cached state between tests.
import media_stack.services.jobs.framework as _jf  # noqa: E402


def _reset_caches() -> None:
    _jf._DISCOVERED_JOBS_CACHE = None
    _jf._DISCOVERED_ALIASES_CACHE = None


class AliasDiscoveryTests(unittest.TestCase):

    def setUp(self) -> None:
        _reset_caches()

    def test_reconcile_aliases_to_bootstrap(self) -> None:
        aliases = _jf.discover_job_aliases()
        self.assertEqual(
            aliases.get("reconcile"), "bootstrap",
            "core.yaml should map reconcile -> bootstrap. The "
            "dashboard's Reconcile button depends on this.",
        )

    def test_aliases_are_a_dict_of_strings(self) -> None:
        aliases = _jf.discover_job_aliases()
        for alias, canonical in aliases.items():
            self.assertIsInstance(alias, str)
            self.assertIsInstance(canonical, str)


class AliasResolutionTests(unittest.TestCase):

    def setUp(self) -> None:
        _reset_caches()

    def test_alias_resolves_to_canonical(self) -> None:
        self.assertEqual(_jf.resolve_alias("reconcile"), "bootstrap")

    def test_unknown_name_passes_through(self) -> None:
        self.assertEqual(
            _jf.resolve_alias("configure-libraries"),
            "configure-libraries",
            "Non-alias names must pass through unchanged.",
        )

    def test_chain_resolves_transitively(self) -> None:
        """If a future contract maps ``foo -> bar`` and ``bar ->
        baz``, ``foo`` should resolve to ``baz``."""
        with mock.patch.object(
            _jf, "discover_job_aliases",
            return_value={"foo": "bar", "bar": "baz"},
        ):
            self.assertEqual(_jf.resolve_alias("foo"), "baz")

    def test_cycle_does_not_hang(self) -> None:
        """A cycle in the alias map (``a -> b -> a``) returns
        whichever name we settle on rather than looping. The hop
        cap inside ``resolve_alias`` is the safety net."""
        with mock.patch.object(
            _jf, "discover_job_aliases",
            return_value={"a": "b", "b": "a"},
        ):
            result = _jf.resolve_alias("a")
            self.assertIn(result, {"a", "b"})


class RunJobAliasTests(unittest.TestCase):

    def setUp(self) -> None:
        _reset_caches()

    def test_run_job_with_alias_finds_canonical_node(self) -> None:
        """End-to-end: ``run_job("reconcile")`` walks the
        ``bootstrap`` tree because that's what the alias resolves
        to. We patch ``JobRunner.run`` to avoid actually firing
        any handlers; the assertion is just that the resolver
        ran and we got the right tree node."""
        captured = {}

        original_runner = _jf.JobRunner

        class _CaptureRunner(original_runner):
            def __init__(self, root, ctx, **kw):
                captured["root_name"] = root.name
                super().__init__(root, ctx, **kw)

            def run(self):
                return {"status": "ok", "captured": captured}

        with mock.patch.object(_jf, "JobRunner", _CaptureRunner):
            result = _jf.run_job("reconcile")

        self.assertEqual(
            captured["root_name"], "bootstrap",
            "run_job(reconcile) didn't resolve to the bootstrap "
            "tree node — alias plumbing is broken.",
        )
        self.assertEqual(result["status"], "ok")


class KnownActionsAliasTests(unittest.TestCase):

    def setUp(self) -> None:
        _reset_caches()

    def test_known_actions_includes_aliases(self) -> None:
        from media_stack.api.services.known_actions import KnownActionsBuilder
        known = KnownActionsBuilder().build()
        self.assertIn(
            "reconcile", known,
            "POST /actions/reconcile would 404 — alias names must "
            "be merged into KNOWN_ACTIONS.",
        )


class DispatchHasNoAliasLogicTests(unittest.TestCase):
    """Belt-and-suspenders: pin that the dispatch source itself
    no longer mentions ``reconcile``. If a refactor accidentally
    re-introduces the special case, this fails before the ratchet
    AST scan runs."""

    def test_dispatch_source_does_not_mention_reconcile(self) -> None:
        path = (
            ROOT / "src" / "media_stack" / "cli" / "commands"
            / "controller_dispatch.py"
        )
        body = path.read_text(encoding="utf-8")
        # Only check inside the function body — comments above the
        # function are fine. Quick heuristic: the literal string
        # 'reconcile' should not appear at all in this file
        # anymore.
        self.assertNotIn(
            '"reconcile"', body,
            "Dispatch source has a 'reconcile' literal — the "
            "alias migrated to contracts/services/core.yaml; the "
            "dispatch should be alias-agnostic.",
        )
        self.assertNotIn(
            "'reconcile'", body,
            "Dispatch source has a 'reconcile' literal — the "
            "alias migrated to contracts/services/core.yaml; the "
            "dispatch should be alias-agnostic.",
        )


if __name__ == "__main__":
    unittest.main()
