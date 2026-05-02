"""Tests for ``infrastructure.promises.assert_eval`` —
ADR-0003 Phase 5e.1.

The shared evaluator is consumed by both the orchestrator dispatcher
and the legacy probe_promises CLI. Pin the contract:

  * Truthy expression → ``(True, "ok")``
  * Falsy expression → ``(False, "assert returned False")``
  * Expression raises → ``(False, "assert eval error: <reason>")``
  * Empty expression → ``(False, "empty assert expression")``
  * Multi-line YAML block scalars work (newlines collapsed to spaces).
  * Generator/comprehension expressions can see scope variables
    (the gotcha that motivated putting names in globals not locals).
  * Builtins outside the allowlist raise NameError, surfaced as
    "assert eval error".
"""

from __future__ import annotations

import pytest

from media_stack.infrastructure.promises.assert_eval import evaluate


class TestBasicShape:
    def test_truthy_returns_ok(self) -> None:
        assert evaluate("1 + 1 == 2", {}) == (True, "ok")

    def test_falsy_returns_assert_failure(self) -> None:
        ok, detail = evaluate("False", {})
        assert ok is False
        assert "assert returned False" in detail

    def test_empty_expression_fails_clean(self) -> None:
        ok, detail = evaluate("", {})
        assert ok is False
        assert "empty" in detail

    def test_whitespace_only_treated_as_empty(self) -> None:
        # Operators sometimes leave assert: |  with just whitespace
        # after deleting; should not crash.
        ok, detail = evaluate("   \n  ", {})
        assert ok is False
        assert "empty" in detail


class TestScope:
    def test_scope_names_visible(self) -> None:
        assert evaluate("response == 42", {"response": 42}) == (True, "ok")

    def test_scope_dict_access(self) -> None:
        assert evaluate(
            "data['x'] == 1 and data['y'] == 2",
            {"data": {"x": 1, "y": 2}},
        ) == (True, "ok")

    def test_generator_expression_sees_scope(self) -> None:
        # The gotcha: generator/comprehension expressions use their
        # own scope and can't see ``locals`` — names MUST live in
        # globals. Pin the fix.
        ok, _ = evaluate(
            "all(x > 0 for x in response)",
            {"response": [1, 2, 3]},
        )
        assert ok is True

    def test_any_with_generator(self) -> None:
        ok, _ = evaluate(
            "any(c in data for c in ('movies', 'tv'))",
            {"data": '"movies" "tv"'},
        )
        assert ok is True


class TestErrors:
    def test_undefined_name_surfaces_as_eval_error(self) -> None:
        ok, detail = evaluate("undefined_thing", {})
        assert ok is False
        assert "assert eval error" in detail
        # Operator should see the actual NameError reason
        assert "undefined_thing" in detail or "not defined" in detail

    def test_division_by_zero_surfaces(self) -> None:
        ok, detail = evaluate("1 / 0", {})
        assert ok is False
        assert "assert eval error" in detail

    def test_builtin_outside_allowlist_blocked(self) -> None:
        # ``open`` is NOT in the allowlist — operators can't read
        # files via the assert expression even if they tried.
        ok, detail = evaluate("open('/etc/passwd').read()", {})
        assert ok is False
        assert "assert eval error" in detail


class TestAllowlistedBuiltins:
    @pytest.mark.parametrize("expr,scope,expected", [
        ("isinstance(response, list)", {"response": [1]}, True),
        ("len(response) >= 1", {"response": [1, 2]}, True),
        ("len(response) >= 5", {"response": [1, 2]}, False),
        ("any(x > 5 for x in response)", {"response": [1, 6]}, True),
        ("all(x > 0 for x in response)", {"response": [1, -1]}, False),
        ("set(response) == {1, 2, 3}", {"response": [3, 2, 1]}, True),
        ("sorted(response) == [1, 2, 3]", {"response": [3, 1, 2]}, True),
    ])
    def test_allowlisted_idioms(self, expr, scope, expected) -> None:
        ok, _ = evaluate(expr, scope)
        assert ok is expected


class TestMultilineExpression:
    def test_yaml_block_scalar_newlines_collapsed(self) -> None:
        # YAML ``|`` block scalars produce multi-line strings; the
        # evaluator collapses newlines to spaces so a single Python
        # expression survives.
        expr = """
        isinstance(response, list)
        and len(response) > 0
        and response[0]['enabled']
        """
        ok, _ = evaluate(expr, {"response": [{"enabled": True}]})
        assert ok is True


class TestProbePromisesAlias:
    def test_legacy_alias_still_resolves(self) -> None:
        # Phase 5e.1 lifted ``_evaluate`` out of the CLI module but
        # left a same-named alias for back-compat. Pin that alias.
        from media_stack.cli.commands.probe_promises import _evaluate
        assert _evaluate is evaluate
