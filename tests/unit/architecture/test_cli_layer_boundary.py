"""ADR-0015 Phase 6 ratchet — CLI layer boundary.

The ``cli/`` tree is split into two sub-packages with a documented
direction-of-imports rule:

* ``cli/commands/``  — entry-point tier (``*_main.py`` console-script
  shims + arg parsing + exit-code translation + signal handling +
  exception types specific to a single CLI's contract).
* ``cli/workflows/`` — service tier (composable workflow services
  + config dataclasses + Protocol contracts + composition root).

Direction-of-imports rule (ADR-0015 Decision section, "The boundary
contract"):

    commands/ MAY import workflows/.  workflows/ MUST NOT import commands/.

This ratchet asserts the rule at module-load time. Phase 4 + Phase 5
landed with zero workflows→commands imports; this test pins that
state so any future drift surfaces at PR-review time instead of as a
"fix the deploy CLI" session months later.

Why a hard zero (no "+ N" buffer)? Because the workflows tier is
explicitly designed to be the leaf in this slice of the dependency
graph — there is no legitimate reason for a workflow service to
import an entry-point shim. The two pre-Phase-4 violations
(``deploy_stack_config_resolution`` mixin + ``deploy_stack_errors``
used from inside ``deploy_config/``) were both resolved during the
ADR-0015 migration: the mixin was deleted, the errors moved to
``cli/workflows/deploy_errors.py``.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = _REPO_ROOT / "src" / "media_stack"
_WORKFLOWS_ROOT = _SRC_ROOT / "cli" / "workflows"

_FORBIDDEN_PREFIX = "media_stack.cli.commands"
_FORBIDDEN_RELATIVE = "media_stack.cli.commands."

_CLI_WORKFLOWS_TO_COMMANDS_RATCHET = 0


class CliWorkflowsImportBoundary(unittest.TestCase):
    """``cli/workflows/`` MUST NOT import from ``cli/commands/``.

    Walks every ``*.py`` under ``cli/workflows/`` and flags any
    top-level OR function-body ``import`` / ``from`` statement that
    resolves to ``media_stack.cli.commands.*``. Both shapes are
    inversions: a deferred import is just as load-bearing as a
    module-level one for the dependency graph.
    """

    def _scan_workflows(self) -> list[tuple[str, str, int]]:
        violations: list[tuple[str, str, int]] = []
        for py in _WORKFLOWS_ROOT.rglob("*.py"):
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                module = self._import_target_module(node)
                if module is not None and self._targets_commands(module):
                    violations.append((
                        str(py.relative_to(_SRC_ROOT)),
                        module,
                        node.lineno,
                    ))
        return violations

    def _import_target_module(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.ImportFrom) and node.module:
            return node.module
        if isinstance(node, ast.Import):
            return node.names[0].name if node.names else None
        return None

    def _targets_commands(self, module: str) -> bool:
        return module == _FORBIDDEN_PREFIX or module.startswith(_FORBIDDEN_RELATIVE)

    def test_workflows_does_not_import_commands(self) -> None:
        violations = self._scan_workflows()
        count = len(violations)
        if count > _CLI_WORKFLOWS_TO_COMMANDS_RATCHET:
            lines = "\n".join(
                f"  {path}:{lineno} imports {module}"
                for path, module, lineno in violations
            )
            self.fail(
                "ADR-0015 Phase 6 ratchet regression: "
                f"{count} workflows -> commands imports "
                f"(ratchet: {_CLI_WORKFLOWS_TO_COMMANDS_RATCHET}).\n"
                "cli/workflows/ MUST NOT import cli/commands/ — see ADR-0015 "
                '"The boundary contract".\n'
                f"{lines}"
            )
        self.assertEqual(
            count,
            _CLI_WORKFLOWS_TO_COMMANDS_RATCHET,
            "Tighten _CLI_WORKFLOWS_TO_COMMANDS_RATCHET — "
            f"actual count is {count}, ratchet was "
            f"{_CLI_WORKFLOWS_TO_COMMANDS_RATCHET}.",
        )


if __name__ == "__main__":
    unittest.main()
