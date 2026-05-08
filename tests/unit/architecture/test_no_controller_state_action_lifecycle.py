"""ADR-0005 Phase 5c.4c architecture ratchet.

Pins that the legacy ``ControllerState`` action-lifecycle surface
is permanently retired:

* No ``current_action`` field on the dataclass.
* No ``action_history`` field on the dataclass.
* No ``start_action`` / ``finish_action`` / ``cancel_action`` /
  ``add_pending`` / ``pop_pending`` / ``get_action`` /
  ``action_running`` methods/properties on the dataclass.
* No production-source reference to any of those names against a
  ``ControllerState`` instance.

If this test fails, you are reintroducing the dual-source-of-truth
shape Phase 5c.4c just retired. The Job framework
(``run_history.get_running_tree`` + ``framework.get_job_history``)
is the canonical view; cancel signals via
``framework.request_cancel``; per-line action tagging reads
``runtime_platform.get_current_action_tag()``.
"""

from __future__ import annotations

import ast
import dataclasses
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.state import ControllerState  # noqa: E402


# Names retired from ``ControllerState``. Every entry MUST stay
# absent or this ratchet trips. To intentionally widen the surface,
# the operator must update this list AND update the migration
# notes in ``docs/adr/ADR-0005.md`` (Phase 5c.4c onward).
_RETIRED_DATACLASS_FIELDS = frozenset({
    "current_action",
    "action_history",
})

_RETIRED_METHODS_OR_PROPERTIES = frozenset({
    "start_action",
    "finish_action",
    "cancel_action",
    "add_pending",
    "pop_pending",
    "get_action",
    "action_running",
})


class TestRetiredDataclassFields(unittest.TestCase):
    def test_no_retired_fields_on_controller_state(self) -> None:
        field_names = {f.name for f in dataclasses.fields(ControllerState)}
        leaked = _RETIRED_DATACLASS_FIELDS & field_names
        self.assertFalse(
            leaked,
            f"ControllerState reintroduced retired fields: {sorted(leaked)}. "
            "ADR-0005 Phase 5c.4c retired these — use the Job framework "
            "(run_history.get_running_tree / framework.get_job_history) "
            "as the source of truth instead.",
        )


class TestRetiredMethods(unittest.TestCase):
    def test_no_retired_methods_or_properties_on_controller_state(self) -> None:
        leaked = {
            name for name in _RETIRED_METHODS_OR_PROPERTIES
            if hasattr(ControllerState, name)
        }
        self.assertFalse(
            leaked,
            f"ControllerState reintroduced retired methods/properties: "
            f"{sorted(leaked)}. ADR-0005 Phase 5c.4c retired these — "
            "cancel via framework.request_cancel, read run history off "
            "the Job framework, and read pending work off the in-process "
            "priority queue.",
        )


class TestNoProductionCallers(unittest.TestCase):
    """AST-walk every production source under ``src/`` and assert
    no module references the retired names as attributes on a
    ``ControllerState`` (or generic ``state`` / ``self``) instance.

    Heuristic: flag any ``Attribute`` access whose ``attr`` is one
    of the retired names — caught at import-time-readable form, no
    type inference required. Docstrings + comments are exempt
    because the AST walker only sees code.

    Allowlist: ``state.py`` itself contains the retired names in
    docstrings + the ratchet comment (``# Method:
    start_action()``); Python AST ignores those. The class
    docstring quotes the retired names in code-fence form
    (``current_action``); those are string literals, not attribute
    accesses, so they don't trigger.

    Architecture-ratchet allowlist: an ALLOWED_FILES set carves
    out the few legitimate sites where the retired names appear
    as attribute accesses on UNRELATED objects (e.g.
    ``ActionRecord.cancel`` is ``self.status = ...; self.error =
    ...`` — the name ``cancel`` is an instance method on the
    value object, not a state-level method). The strict default
    is empty; widen only with attribution in the commit message.
    """

    # ``ActionRecord`` itself defines ``.cancel()`` as a value-object
    # state-transition method, distinct from the retired
    # ``ControllerState.cancel_action``. The retired-name set
    # excludes ``cancel`` for that reason — only the ``_action``-
    # suffixed names ever lived on the state class.

    _SRC = ROOT / "src" / "media_stack"

    # Files that can legitimately reference the retired names —
    # currently empty post-cleanup. Adding a path here requires
    # ratchet-bump attribution in the commit message.
    _ALLOWED_FILES: frozenset[str] = frozenset()

    def _walk_attrs(self, tree: ast.AST):
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                yield node

    def test_no_production_caller_uses_retired_attribute(self) -> None:
        violations: list[tuple[str, int, str]] = []
        for py_file in self._SRC.rglob("*.py"):
            rel = str(py_file.relative_to(ROOT))
            if rel in self._ALLOWED_FILES:
                continue
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for attr_node in self._walk_attrs(tree):
                attr_name = attr_node.attr
                if attr_name not in _RETIRED_METHODS_OR_PROPERTIES:
                    continue
                # ``state.py`` itself defines ``ActionRecord.cancel``
                # but not the retired ``cancel_action`` method —
                # the ratchet only checks for the latter, so the
                # value object is naturally exempt.
                violations.append((rel, attr_node.lineno, attr_name))
        self.assertFalse(
            violations,
            "Production source still references retired "
            "ControllerState attributes:\n"
            + "\n".join(f"  {p}:{ln}: .{name}" for p, ln, name in violations),
        )

    def test_no_production_caller_uses_retired_field(self) -> None:
        violations: list[tuple[str, int, str]] = []
        for py_file in self._SRC.rglob("*.py"):
            rel = str(py_file.relative_to(ROOT))
            if rel in self._ALLOWED_FILES:
                continue
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for attr_node in self._walk_attrs(tree):
                attr_name = attr_node.attr
                if attr_name not in _RETIRED_DATACLASS_FIELDS:
                    continue
                violations.append((rel, attr_node.lineno, attr_name))
        self.assertFalse(
            violations,
            "Production source still references retired "
            "ControllerState fields:\n"
            + "\n".join(f"  {p}:{ln}: .{name}" for p, ln, name in violations),
        )


if __name__ == "__main__":
    unittest.main()
