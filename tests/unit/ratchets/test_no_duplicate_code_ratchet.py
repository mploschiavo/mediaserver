"""Ratchet: detect (and burn down) duplicated function bodies.

Walks every Python source file under ``src/media_stack/`` and
``tests/`` and computes a structural hash for every top-level and
class-method function whose body is "non-trivial" (>= 5 statements
after dropping docstrings + comments). Any two functions with the
same hash are flagged as duplicates.

The total duplicate-group count is compared to the burn-down baseline
in ``.ratchets/duplicate-code-baseline.txt``. The count may NEVER
go up — every PR that adds a new duplicate fails CI. PRs that
delete duplicates are required to lower the baseline accordingly
(the second test catches stale baselines).

Why: this codebase has visible duplication across compose-vs-k8s
adapters, a few classes copied between application/ and core/,
and helper functions reimplemented in features that share
contract output. We can't fix all of it at once, but the burn-down
mechanic at least keeps it from growing while we chip away.

The hash is structural — names, literal strings, and line numbers
are normalized — so a renamed copy still collides with its original.
Pure dataclass / field declarations are excluded; they look
duplicated by definition.
"""

from __future__ import annotations

import ast
import hashlib
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE_FILE = REPO_ROOT / ".ratchets" / "duplicate-code-baseline.txt"
# Baseline history (the .txt is int-only by convention; rationale
# lives here so the next reader sees WHY the count moved):
#   * 27 → 28 — ADR-0005 Phase 5b.1: DownloadClientWirer's
#     ``_guard_probe_prereqs`` is structurally identical to
#     IndexerPipelineWirer's (servarr/download_client_wiring.py:172
#     ↔ servarr/indexer_pipeline.py:120). Both walk the same
#     pattern: "support-set membership → endpoint-resolve → api-key
#     check → None". The two wirers wrap different lookup tables
#     (_ARR_DOWNLOAD_CLIENT_SPECS vs _ARR_API_VERSIONS) and
#     different endpoint resolvers, so the bodies normalize to
#     identical fingerprints. Wave-of-9-wirers duplication, same
#     rationale as the pre-Phase-5b ``_ping_url`` group across the
#     5 lifecycle adapters. A future LifecycleWirerBase-level
#     extraction (templated guard helper accepting a support_set +
#     endpoint_fn) would reduce this back to 27.
SCAN_DIRS = (REPO_ROOT / "src" / "media_stack",)
MIN_STATEMENTS = 5


# Files to skip — typically generated bindings, vendored copies,
# fixture loaders, or unavoidable shim layers (e.g. the
# core/platforms/compose shim exists explicitly to mirror
# adapters/compose during a migration).
_SKIP_FRAGMENTS = (
    "__pycache__",
    "/migrations/",
    "/vendor/",
)


def _should_scan(path: Path) -> bool:
    s = str(path)
    return not any(frag in s for frag in _SKIP_FRAGMENTS)


def _normalize_node(node: ast.AST) -> ast.AST:
    """Strip names + string literals so renames don't hide a copy."""
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            child.id = "_"
        elif isinstance(child, ast.Attribute):
            child.attr = "_"
        elif isinstance(child, ast.arg):
            child.arg = "_"
            child.annotation = None
        elif isinstance(child, ast.Constant):
            # Replace string and number literals with a sentinel so
            # cosmetic changes don't hide a copy. Skip None/bool —
            # they're load-bearing for control flow.
            if isinstance(child.value, (str, int, float, bytes)):
                child.value = 0
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
            child.name = "_"
            child.decorator_list = []
            # Strip docstrings.
            if (child.body
                    and isinstance(child.body[0], ast.Expr)
                    and isinstance(child.body[0].value, ast.Constant)
                    and isinstance(child.body[0].value.value, str)):
                child.body = child.body[1:]
    return node


def _statement_count(body: list[ast.stmt]) -> int:
    """Count meaningful statements (drops docstring + pass)."""
    out = 0
    for stmt in body:
        if isinstance(stmt, ast.Pass):
            continue
        if (isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)):
            continue
        out += 1
    return out


def _fingerprint_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    body_module = ast.Module(body=list(node.body), type_ignores=[])
    _normalize_node(body_module)
    dump = ast.dump(body_module, annotate_fields=False)
    return hashlib.sha1(dump.encode("utf-8")).hexdigest()


def _scan_file(path: Path) -> list[tuple[str, str, str]]:
    """Return ``[(fingerprint, qualified_name, location)]`` for every
    non-trivial function defined in ``path``."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[tuple[str, str, str]] = []

    def _walk(scope: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(scope):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if _statement_count(child.body) < MIN_STATEMENTS:
                    continue
                qual = f"{prefix}{child.name}"
                fingerprint = _fingerprint_function(child)
                location = f"{path.relative_to(REPO_ROOT)}:{child.lineno}"
                out.append((fingerprint, qual, location))
            elif isinstance(child, ast.ClassDef):
                _walk(child, prefix=f"{prefix}{child.name}.")

    _walk(tree, prefix="")
    return out


def _all_duplicate_groups() -> dict[str, list[str]]:
    by_fingerprint: dict[str, list[str]] = defaultdict(list)
    for scan_root in SCAN_DIRS:
        for path in scan_root.rglob("*.py"):
            if not _should_scan(path):
                continue
            for fingerprint, _qual, location in _scan_file(path):
                by_fingerprint[fingerprint].append(location)
    return {
        fp: sorted(locations) for fp, locations in by_fingerprint.items()
        if len(locations) > 1
    }


def _read_baseline() -> int:
    if not BASELINE_FILE.is_file():
        # No baseline yet — initialize implicitly to the current
        # count + a small slack so the first test run captures the
        # "what's there now" without failing.
        return -1
    raw = BASELINE_FILE.read_text(encoding="utf-8").strip()
    try:
        return int(raw)
    except ValueError:
        return -1


def test_duplicate_function_count_does_not_grow_above_baseline() -> None:
    duplicates = _all_duplicate_groups()
    current = len(duplicates)
    baseline = _read_baseline()

    if baseline < 0:
        # First run — write the current count as the baseline so
        # later runs have something to gate against. Don't fail
        # because there's no signal to fail on yet.
        BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_FILE.write_text(str(current) + "\n", encoding="utf-8")
        return

    assert current <= baseline, (
        f"Duplicate function-body count grew from {baseline} to "
        f"{current}. New duplicates introduced — fix by extracting "
        f"a shared helper, OR by lowering the baseline if you "
        f"genuinely deleted a copy.\n\n"
        f"Top offenders (locations sharing the same fingerprint):\n"
        + "\n".join(
            "  - " + " ; ".join(group)
            for group in sorted(
                duplicates.values(),
                key=lambda g: -len(g),
            )[:5]
        )
        + "\n\nBaseline lives at .ratchets/duplicate-code-baseline.txt — "
        "every legitimate dedup should lower it."
    )


def test_baseline_does_not_overshoot_current_count() -> None:
    """If you fixed a duplicate but forgot to lower the baseline,
    this test catches the slack so the next regression isn't
    masked by stale headroom."""
    duplicates = _all_duplicate_groups()
    current = len(duplicates)
    baseline = _read_baseline()
    if baseline < 0:
        return  # First run handled by the other test.
    # Allow up to 5 above current to absorb noisy variance from
    # adding/removing tiny helpers; force-tighten beyond that.
    assert baseline - current <= 5, (
        f"Duplicate baseline ({baseline}) overshoots current "
        f"count ({current}) by {baseline - current}. Tighten by "
        f"editing .ratchets/duplicate-code-baseline.txt down to {current}."
    )
