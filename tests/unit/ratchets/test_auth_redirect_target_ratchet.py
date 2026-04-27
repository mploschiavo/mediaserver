"""Ratchets that keep the Authelia redirect plumbing single-sourced.

**Rule 1 — no path-prefix mount.** Every Authelia redirect goes
through the dedicated portal host (``auth.<base>``), never the
path-prefix mount (``/app/authelia/``). The path-prefix mount has
two real failure modes:

1. **Lua filter rewrite**. The compose/k8s Envoy config injects a
   "prefix patch" Lua filter that rewrites relative URLs to live under
   the current path prefix. Authelia's portal SPA, when served at
   ``/app/authelia/``, has its own router routes (``/login``,
   ``/logout``, ``/settings``). The Lua filter mangles them — operators
   land at ``/app/authelia/<arbitrary>`` with no working login form.

2. **Cookie scope mismatch**. The ``authelia_session`` cookie is set
   with ``Domain=<base>`` (e.g. ``iomio.io``). Logout responses from
   the path-prefix mount tend to clear cookies host-only (no domain
   attribute), leaving the parent-domain cookie alive. Operators
   describe the symptom as "I sign out and can't sign back in until
   I close the browser."

**Rule 2 — single source of truth.** Only the ``auth-portal`` helper
itself may call the underlying ``resolveAuthPortalUrl(hostname)``
resolver. Every other consumer goes through ``authPortal()`` (no
args, memoized). Without this rule the URL gets re-derived in 5+
places — operators have to update each one when the portal hostname
changes, and the audit ratchet (Rule 1) above can't see drift across
files. Operator's words: "if you have the same url, why don't load
that from an object — and the object has 1 value instead of having
code have it hardcoded in 5+ places?"

Allowed uses of the literal ``/app/authelia`` (Rule 1) are limited
to: loop-guard checks, comments/docstrings, test fixtures.

Allowed callers of ``resolveAuthPortalUrl`` (Rule 2) are limited
to: the helper module itself and its unit tests that exercise
hostname-derivation edge cases.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
UI_SRC = REPO_ROOT / "ui" / "src"

# Lines that legitimately mention the path-prefix mount.
ALLOWED_FILES = frozenset({
    # Helper that documents the migration away from the path-prefix
    # mount; comments mention it for context.
    "ui/src/lib/auth-portal.ts",
    # Loop-guard tests assert behavior on legacy paths.
    "ui/src/lib/auth-redirect.test.ts",
    # Test for ApiErrorTile / UserMenu may mention the legacy URL.
    "ui/src/components/layout/UserMenu.test.tsx",
    # OpenAPI snapshot includes a documentation example URL.
    "ui/src/api/types.ts",
    # Path-audit ratchet itself, if it ever needs to mention the
    # string for documentation.
})

# Regex that flags actual redirect targets, not comments or docstring
# mentions. We match string literals containing ``/app/authelia`` that
# are NOT preceded by a comment marker on the same line.
RE_REDIRECT_TARGET = re.compile(
    r'(?<!//)\s*(?:href|to|signInPath|location\.(?:replace|assign|href))\s*'
    r'(?:=|:)\s*[`"\']/app/authelia',
)
# Plain literal — flags any unguarded ``"/app/authelia..."`` outside
# allowed files. Catches ``href`` on bare anchors.
RE_BARE_LITERAL = re.compile(r'["\'`]/app/authelia[^"\'`]*["\'`]')


def _in_comment(line: str, idx: int) -> bool:
    """Heuristic: the match starts after a ``//`` or inside a doc
    block. Avoids flagging comment text."""
    upto = line[:idx]
    if "//" in upto:
        return True
    stripped = line.lstrip()
    return stripped.startswith(("*", "/*", "//", "*/"))


def _is_allowed(rel_path: str) -> bool:
    return rel_path in ALLOWED_FILES


def _scan_file(path: Path) -> list[tuple[int, str]]:
    """Return ``(line_no, line)`` for each unguarded literal match."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    flagged: list[tuple[int, str]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        m = RE_BARE_LITERAL.search(line)
        if m is None:
            continue
        if _in_comment(line, m.start()):
            continue
        # Skip lines that match the legacy loop-guard pattern.
        if "startsWith" in line and "/app/authelia" in line:
            continue
        flagged.append((line_no, line.strip()))
    return flagged


def _scan_ui() -> dict[str, list[tuple[int, str]]]:
    findings: dict[str, list[tuple[int, str]]] = {}
    if not UI_SRC.is_dir():
        return findings
    for ext in ("*.ts", "*.tsx"):
        for path in UI_SRC.rglob(ext):
            rel = str(path.relative_to(REPO_ROOT))
            if _is_allowed(rel):
                continue
            flagged = _scan_file(path)
            if flagged:
                findings[rel] = flagged
    return findings


def test_no_path_prefix_authelia_redirect_targets() -> None:
    """No new ``/app/authelia/...`` redirect targets in the UI."""
    findings = _scan_ui()
    if findings:
        lines = []
        for rel, hits in sorted(findings.items()):
            for line_no, snippet in hits:
                lines.append(f"  {rel}:{line_no}  {snippet}")
        details = "\n".join(lines)
        raise AssertionError(
            "Authelia redirect targets must use ``authPortal()`` "
            "from ``ui/src/lib/auth-portal.ts`` — the dedicated portal "
            "subdomain (``auth.<base>``), NOT the path-prefix mount "
            "(``/app/authelia/...``). The path-prefix mount is mangled "
            "by the Lua prefix filter and has cookie-scope issues that "
            "leave operators stuck at ``/app/authelia/<arbitrary>`` with "
            "no working login form. Offending references:\n"
            f"{details}\n\n"
            "Resolution: import ``authPortal`` and use "
            "``${authPortal()}/...`` instead. If this file legitimately "
            "needs the legacy literal (loop-guard / test fixture / "
            "comment), add it to ``ALLOWED_FILES`` in this ratchet with "
            "a one-line rationale."
        )


# Files allowed to call the bare resolver (which requires a hostname
# argument every time and is therefore the path-prefix risk). Every
# other consumer must go through the memoized ``authPortal()``
# accessor so the URL is computed exactly once per page load.
RESOLVER_ALLOWED_FILES = frozenset({
    # The helper that owns both the resolver and the cached
    # accessor. Internally calls the resolver from inside
    # ``authPortal()`` to populate the cache.
    "ui/src/lib/auth-portal.ts",
    # Unit test for hostname-derivation edge cases — must exercise
    # the resolver directly with synthetic hostnames.
    "ui/src/lib/auth-portal.test.ts",
})


# Match ``resolveAuthPortalUrl(`` as a function call (not just an
# import). Comments and docstrings are filtered separately.
RE_RESOLVER_CALL = re.compile(r"\bresolveAuthPortalUrl\s*\(")


def _scan_resolver_callers() -> dict[str, list[tuple[int, str]]]:
    findings: dict[str, list[tuple[int, str]]] = {}
    if not UI_SRC.is_dir():
        return findings
    for ext in ("*.ts", "*.tsx"):
        for path in UI_SRC.rglob(ext):
            rel = str(path.relative_to(REPO_ROOT))
            if rel in RESOLVER_ALLOWED_FILES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            hits: list[tuple[int, str]] = []
            for line_no, line in enumerate(text.splitlines(), start=1):
                m = RE_RESOLVER_CALL.search(line)
                if m is None:
                    continue
                if _in_comment(line, m.start()):
                    continue
                # Allow imports — only the function calls are
                # forbidden outside the helper.
                if "import" in line and "from" in line:
                    continue
                hits.append((line_no, line.strip()))
            if hits:
                findings[rel] = hits
    return findings


def test_resolver_only_called_inside_helper() -> None:
    """``resolveAuthPortalUrl(hostname)`` must only be called inside
    the ``auth-portal`` helper. Every other consumer goes through
    ``authPortal()`` so the URL is computed once and memoized."""
    findings = _scan_resolver_callers()
    if findings:
        lines = []
        for rel, hits in sorted(findings.items()):
            for line_no, snippet in hits:
                lines.append(f"  {rel}:{line_no}  {snippet}")
        details = "\n".join(lines)
        raise AssertionError(
            "Direct calls to ``resolveAuthPortalUrl(hostname)`` are "
            "forbidden outside ``ui/src/lib/auth-portal.ts``. The "
            "URL is the same on every consumer; recomputing it each "
            "time means 5+ places to update when the portal hostname "
            "changes (operator's exact ask: 'why don't you load that "
            "from an object?'). Offending callers:\n"
            f"{details}\n\n"
            "Resolution: ``import { authPortal } from "
            "\"@/lib/auth-portal\"`` and call ``authPortal()`` (no "
            "args, memoized) instead. If this file legitimately "
            "needs the unmemoized resolver (e.g. a unit test with "
            "synthetic hostnames), add it to "
            "``RESOLVER_ALLOWED_FILES`` with a one-line rationale."
        )
