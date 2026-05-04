"""Burndown ratchet for ADR-0007 Phase 2.

Each domain migration in ``api/routes/<domain>.py`` deletes one or
more ``elif path == ...`` branches from
``handlers_get.GetRequestHandler.handle()`` and
``handlers_post.PostRequestHandler.handle()``. This ratchet pins
the count so it can only go DOWN.

When the count reaches zero, ADR-0007 Phase 2 is complete: the
legacy ``handle()`` chains can be deleted entirely, and
``server.py`` no longer needs the ``NO_MATCH`` fall-through.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[3]
_HANDLERS_GET = _REPO_ROOT / "src" / "media_stack" / "api" / "handlers_get.py"
_HANDLERS_POST = _REPO_ROOT / "src" / "media_stack" / "api" / "handlers_post.py"
_BASELINE = (
    _REPO_ROOT / ".ratchets" / "router-elif-chain-baseline.txt"
)


class _ElifPathBranchCounter(ast.NodeVisitor):
    """Counts ``elif`` clauses whose test compares ``path`` to a
    string constant or string-tuple — the shape ADR-0007 Phase 2
    is burning down."""

    def __init__(self) -> None:
        self.count = 0

    def visit_Compare(self, node: ast.Compare) -> None:
        # Match ``path == "..."`` and ``path in (...,)``.
        if (
            isinstance(node.left, ast.Name)
            and node.left.id == "path"
            and len(node.ops) == 1
        ):
            op = node.ops[0]
            cmp = node.comparators[0]
            if isinstance(op, ast.Eq) and isinstance(cmp, ast.Constant):
                self.count += 1
            elif isinstance(op, ast.In) and isinstance(
                cmp, (ast.Tuple, ast.List),
            ):
                self.count += 1
        self.generic_visit(node)


class _PathStartswithCounter(ast.NodeVisitor):
    """Counts ``path.startswith("...")`` calls in the chain. These
    are the prefix-match branches that need migrating to
    parameterized routes (``/api/users/{user_id}``)."""

    def __init__(self) -> None:
        self.count = 0

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "path"
            and node.func.attr == "startswith"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            self.count += 1
        self.generic_visit(node)


def _count_chain_branches(file_path: Path) -> int:
    """Return ``elif path == ...`` + ``path.startswith(...)`` total
    in the file. Both forms count toward burndown."""
    tree = ast.parse(file_path.read_text(encoding="utf-8"))
    eq_counter = _ElifPathBranchCounter()
    eq_counter.visit(tree)
    starts_counter = _PathStartswithCounter()
    starts_counter.visit(tree)
    return eq_counter.count + starts_counter.count


class TestRouterElifChainBurndown:
    """``handlers_get.py`` + ``handlers_post.py`` ``elif path == ...``
    + ``path.startswith(...)`` branch counts only go DOWN. Each
    Phase 2 commit migrates a domain's branches into
    ``api/routes/<domain>.py``."""

    def test_total_branch_count_below_baseline(self) -> None:
        get_count = _count_chain_branches(_HANDLERS_GET)
        post_count = _count_chain_branches(_HANDLERS_POST)
        total = get_count + post_count

        if not _BASELINE.is_file():
            _BASELINE.parent.mkdir(parents=True, exist_ok=True)
            _BASELINE.write_text(f"{total}\n")
            pytest.skip(
                f"Seeded ADR-0007 Phase 2 baseline at {total} "
                f"branches (handlers_get={get_count}, "
                f"handlers_post={post_count})",
            )

        baseline = int(_BASELINE.read_text().strip())
        assert total <= baseline, (
            f"ADR-0007 Phase 2 burndown regressed: {baseline} → "
            f"{total} (handlers_get={get_count}, "
            f"handlers_post={post_count}). Each domain migration "
            f"into ``api/routes/`` should DELETE the corresponding "
            f"elif/startswith branches from the legacy chain. To "
            f"accept the new count: edit {_BASELINE} — but the "
            f"intent of this ratchet is the OPPOSITE direction."
        )
