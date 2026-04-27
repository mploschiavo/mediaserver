"""Phase 2 TypeScript ratchets — fills gaps in the wishlist.

Already covered elsewhere:
  * ``any`` count → ``ts-any-usage``
  * ``@ts-ignore`` count → hard gate
  * ``@ts-expect-error`` count → ``ts-expect-error``
  * Non-null assertions → ``ts-non-null-assertion``
  * Console statements → ``ts-console-statements``

This file adds:
  * Floating promises (a common React/async bug)
  * React components over N lines (split-this signal)
  * Props interfaces over N fields (parameter object signal)
  * Duplicate string literal values (extract to constants)
  * Files without strict type coverage (any leaks)
  * Implicit return any (heuristic — exported function with no
    explicit return type)
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
UI_SRC = REPO_ROOT / "ui" / "src"
RATCHETS_DIR = REPO_ROOT / ".ratchets"


def _load_baseline(name: str) -> int | None:
    p = RATCHETS_DIR / f"{name}-baseline.txt"
    if not p.is_file():
        return None
    try:
        return int(p.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def _seed_baseline(name: str, value: int) -> None:
    RATCHETS_DIR.mkdir(parents=True, exist_ok=True)
    (RATCHETS_DIR / f"{name}-baseline.txt").write_text(
        f"{value}\n", encoding="utf-8",
    )


def _enforce_burndown(name: str, current: int, *, hint: str) -> None:
    baseline = _load_baseline(name)
    if baseline is None:
        _seed_baseline(name, current)
        return
    if current > baseline:
        raise AssertionError(
            f"{name}: regressed from {baseline} → {current}.\n{hint}"
        )


def _iter_ts_files(*, skip_test_files: bool = True) -> list[Path]:
    if not UI_SRC.is_dir():
        return []
    out = []
    for path in UI_SRC.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in {".ts", ".tsx"}:
            continue
        if any(p in {"node_modules", "dist", "build"} for p in path.parts):
            continue
        if skip_test_files and (
            ".test." in path.name or path.name.endswith(".d.ts")
        ):
            continue
        out.append(path)
    return out


# ---------------------------------------------------------------------------
# 1. Floating promises
# ---------------------------------------------------------------------------


# Heuristic: a statement-level expression that calls a function
# whose name suggests it returns a promise (``fetch*``, ``send*``,
# ``post*``, ``mutate*``, ``refetch*``, ``invalidate*``, ``save*``,
# ``delete*``, ``update*``) and is NOT prefixed with ``await``,
# ``void``, or assigned. Best-effort regex; misses indirect calls
# but catches the common patterns.
_RE_FLOATING_PROMISE = re.compile(
    r"^\s*(?!(?:await|return|void|const|let|var|throw)\s)"
    r"(?:[A-Za-z_$][\w$]*\.)*"
    r"(?:fetch|send|post|put|delete|mutate|mutateAsync|refetch|"
    r"invalidate|save|update|reload|trigger|emit)\b[^=]*\(",
    re.MULTILINE,
)


def test_burndown_floating_promises() -> None:
    """Statement-level calls to async functions without ``await``,
    ``void``, or assignment. Each one is a fire-and-forget that may
    error after the calling component unmounts (React warning) or
    finish in a different order than expected (race)."""
    count = 0
    for path in _iter_ts_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # Strip block comments so JSDoc examples don't get counted.
        text = re.sub(r"/\*[\s\S]*?\*/", "", text)
        # Strip single-line comments.
        text = re.sub(r"//.*$", "", text, flags=re.MULTILINE)
        for line in text.splitlines():
            if _RE_FLOATING_PROMISE.search(line):
                count += 1
    _enforce_burndown(
        "floating-promises",
        count,
        hint=(
            "Either ``await`` the call (preferred — caller knows "
            "when it completes), prefix with ``void`` (explicit "
            "fire-and-forget; lint-clean), or store the promise in "
            "a variable + handle errors. Floating promises that "
            "reject become unhandled-rejection warnings or — worse "
            "— silently lost."
        ),
    )


# ---------------------------------------------------------------------------
# 2. React components over N lines
# ---------------------------------------------------------------------------


def _react_component_files() -> list[Path]:
    """Heuristic: ``.tsx`` files under ``features/`` or ``components/``
    that ``export`` a function whose name starts with a capital
    letter (React-component naming convention)."""
    out = []
    for path in _iter_ts_files():
        if path.suffix != ".tsx":
            continue
        rel = str(path.relative_to(UI_SRC)).replace("\\", "/")
        if not (rel.startswith("features/") or rel.startswith("components/") or rel.startswith("routes/")):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if re.search(r"export\s+(?:default\s+)?function\s+[A-Z]", text):
            out.append(path)
        elif re.search(r"export\s+const\s+[A-Z][\w]*\s*[:=]", text):
            out.append(path)
    return out


def test_burndown_react_components_over_300_lines() -> None:
    """``.tsx`` files exporting a React component with > 300 lines
    of source. Above this the component is doing too much — split
    sub-components out, lift state into a custom hook, or both."""
    count = 0
    for path in _react_component_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if text.count("\n") + 1 > 300:
            count += 1
    _enforce_burndown(
        "react-components-over-300-lines",
        count,
        hint=(
            "Split the component. Common moves: (1) extract sub-"
            "components for sections of the JSX; (2) move "
            "data-fetching + transforms into a custom hook; "
            "(3) move pure helpers (formatters, mappers) to a "
            "sibling module."
        ),
    )


# ---------------------------------------------------------------------------
# 3. Props interfaces over N fields
# ---------------------------------------------------------------------------


_RE_INTERFACE_DECL = re.compile(
    r"interface\s+(\w+Props)\s*\{([^}]*)\}",
    re.DOTALL,
)


def test_burndown_props_interfaces_over_8_fields() -> None:
    """``interface FooProps { ... }`` with > 8 fields. Components
    with that many props usually want to be split, or the props
    grouped into a config object."""
    count = 0
    for path in _iter_ts_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for m in _RE_INTERFACE_DECL.finditer(text):
            body = m.group(2)
            # Each field looks like ``name?: type;`` or
            # ``name: type;`` separated by newlines or semicolons.
            # Strip block comments + single-line comments first.
            cleaned = re.sub(r"/\*[\s\S]*?\*/", "", body)
            cleaned = re.sub(r"//.*$", "", cleaned, flags=re.MULTILINE)
            field_lines = [
                ln.strip() for ln in cleaned.splitlines()
                if ln.strip() and not ln.strip().startswith("//")
            ]
            # Heuristic: count lines containing ``:``, not the {}.
            field_count = sum(
                1 for ln in field_lines
                if ":" in ln and not ln.startswith("//")
            )
            if field_count > 8:
                count += 1
    _enforce_burndown(
        "props-interfaces-over-8-fields",
        count,
        hint=(
            "8+ props is a smell. Common refactors: "
            "(1) compose smaller components, "
            "(2) merge related props into a single config object "
            "({ pagination: { page, size, total } } instead of "
            "page/size/total at top), or (3) lift shared state into "
            "context."
        ),
    )


# ---------------------------------------------------------------------------
# 4. Duplicate string literal values
# ---------------------------------------------------------------------------


# Heuristic: count string literals (length >= 8) that appear in 5+
# distinct files. These are constants in waiting.
_RE_TS_STRING_LITERAL = re.compile(
    r'"([^"\\\n]{8,200})"|\'([^\'\\\n]{8,200})\'|`([^`\\\n${}]{8,200})`',
)


def test_burndown_duplicate_string_literals_5plus_files() -> None:
    """A non-trivial string literal appearing in 5+ files should be
    a shared constant. Reduces drift (one "completed" vs "complete"
    discrepancy is a runtime bug)."""
    by_literal: dict[str, set[Path]] = {}
    for path in _iter_ts_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # Strip block + line comments to avoid counting docs.
        text = re.sub(r"/\*[\s\S]*?\*/", "", text)
        text = re.sub(r"//.*$", "", text, flags=re.MULTILINE)
        for m in _RE_TS_STRING_LITERAL.finditer(text):
            literal = m.group(1) or m.group(2) or m.group(3) or ""
            if not literal:
                continue
            # Skip path-like literals (already covered) and obvious
            # JSX class strings.
            if literal.startswith(("/", "./", "../", "#", "data-")):
                continue
            if any(c in literal for c in (" ", "\\")):
                continue
            by_literal.setdefault(literal, set()).add(path)
    count = sum(1 for paths in by_literal.values() if len(paths) >= 5)
    _enforce_burndown(
        "duplicate-string-literals-5plus-files",
        count,
        hint=(
            "Move the literal to a shared constants module. Reading "
            "different spellings in different files is a runtime "
            "bug class — ``\"completed\"`` in one place and "
            "``\"complete\"`` in another silently disagree."
        ),
    )


# ---------------------------------------------------------------------------
# 5. Files importing/declaring `any` (any type leakage surface)
# ---------------------------------------------------------------------------


_RE_ANY_USE = re.compile(r":\s*any\b|<any>|\bas\s+any\b")


def test_burndown_files_with_any_usage() -> None:
    """Number of FILES (not occurrences) where ``any`` appears.
    Distinct from the existing ``ts-any-usage`` count — that's the
    occurrence total. This one tracks blast radius: ``any`` in one
    big file is fixable in one PR; ``any`` in 30 files is a
    cross-cutting refactor."""
    files_with_any = 0
    for path in _iter_ts_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # Strip comments first.
        text = re.sub(r"/\*[\s\S]*?\*/", "", text)
        text = re.sub(r"//.*$", "", text, flags=re.MULTILINE)
        if _RE_ANY_USE.search(text):
            files_with_any += 1
    _enforce_burndown(
        "files-with-any-usage",
        files_with_any,
        hint=(
            "Replace ``any`` with a real type, ``unknown`` + "
            "narrowing, or a generic. Tracking files-not-occurrences "
            "lets us see when a refactor consolidates all the ``any`` "
            "into one place worth fixing properly."
        ),
    )


# ---------------------------------------------------------------------------
# 6. Implicit return any (heuristic)
# ---------------------------------------------------------------------------


# Match ``export function name(args)`` (no return type) as a
# heuristic for "this might return any". Doesn't catch arrow
# functions; tsc would do better but the AST cost is high.
_RE_EXPORTED_FUNC_NO_RETURN_TYPE = re.compile(
    r"^export\s+(?:async\s+)?function\s+\w+\s*(?:<[^>]+>)?\s*\([^)]*\)\s*\{",
    re.MULTILINE,
)


def test_burndown_exported_functions_without_return_type() -> None:
    """Exported function declarations without an explicit return
    type. TypeScript will infer one — but inferred ``any`` from a
    chain of helpers spreads ``any`` through every caller's
    inference."""
    count = 0
    for path in _iter_ts_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        text = re.sub(r"/\*[\s\S]*?\*/", "", text)
        text = re.sub(r"//.*$", "", text, flags=re.MULTILINE)
        count += len(_RE_EXPORTED_FUNC_NO_RETURN_TYPE.findall(text))
    _enforce_burndown(
        "exported-functions-without-return-type",
        count,
        hint=(
            "Add an explicit return type annotation to exported "
            "functions: ``export function foo(): X { ... }``. "
            "Inferred return types are a hidden contract — a "
            "refactor can silently widen them, breaking callers' "
            "type guarantees."
        ),
    )
