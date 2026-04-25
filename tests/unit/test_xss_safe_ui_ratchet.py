"""Ratchet: no XSS-unsafe DOM sinks in extracted dashboard JS.

Scope
-----
Applies to the **extracted** tab JS files under
``src/media_stack/api/static/tab_*.js`` — the new session-visibility
tabs. The legacy monolithic ``dashboard.html`` is NOT covered here
(a separate, larger migration tracks bringing it under this gate —
see ``docs/roadmap/session-visibility-followups.md``).

Banned patterns (enforced at grep time):

- ``.innerHTML =`` with anything non-literal on the right-hand side.
- ``document.write``, ``document.writeln``.
- ``eval(``, ``new Function(`` — JS-from-strings entry points.
- ``setTimeout("..."`` / ``setInterval("..."`` — code-as-string timers.
- ``outerHTML =`` with anything — equivalent to innerHTML for XSS.
- ``insertAdjacentHTML(`` — literal string would be fine but the
  ratchet flags the call shape to force reviewers to justify it.

The third-party ``swagger-ui-bundle.js`` is in the same directory
but is not under our control; it's explicitly excluded by basename.

Why a ratchet rather than inline comments
-----------------------------------------
Every failing test is specific: it names the file + line + banned
token. An inline comment can be forgotten; a grep always runs. New
UI code can't accidentally introduce a DOM sink without this test
lighting up.

Trusted Types, CSP, and this ratchet form a three-layer defence:
- CSP (``require-trusted-types-for 'script'`` in
  ``STRICT_POLICY``) refuses at runtime.
- Trusted Types policy filters at parse time.
- This ratchet refuses at CI time.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = ROOT / "src" / "media_stack" / "api" / "static"

_THIRD_PARTY_BASENAMES: frozenset[str] = frozenset({
    "swagger-ui-bundle.js",
    "swagger-ui-standalone-preset.js",
    "swagger-ui.css",
})

# Banned pattern -> (regex, human-readable reason).
_BANNED_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "innerHTML_assignment",
        re.compile(r"\.innerHTML\s*="),
        "use textContent or an escapeHtml(...) helper — raw innerHTML "
        "assignments are the #1 XSS sink",
    ),
    (
        "outerHTML_assignment",
        re.compile(r"\.outerHTML\s*="),
        "use DOM APIs (replaceChild, insertBefore) — outerHTML is an "
        "innerHTML equivalent for XSS",
    ),
    (
        "document_write",
        re.compile(r"document\.write(?:ln)?\s*\("),
        "document.write blocks page parsing and is a classic XSS sink",
    ),
    (
        "eval_call",
        re.compile(r"(?<!\w)eval\s*\("),
        "eval() executes arbitrary code — never needed in dashboard JS",
    ),
    (
        "new_function",
        re.compile(r"new\s+Function\s*\("),
        "new Function(...) is eval-equivalent — always refactor to real "
        "functions",
    ),
    (
        "setTimeout_string",
        re.compile(r"setTimeout\s*\(\s*['\"]"),
        "setTimeout with a string argument is eval — pass a function",
    ),
    (
        "setInterval_string",
        re.compile(r"setInterval\s*\(\s*['\"]"),
        "setInterval with a string argument is eval — pass a function",
    ),
    (
        "insertAdjacentHTML",
        re.compile(r"\.insertAdjacentHTML\s*\("),
        "insertAdjacentHTML bypasses sanitisers even with literal "
        "templates — use createElement + appendChild",
    ),
]


# Files that legitimately use a pattern and have been reviewed.
# Format: "<relpath>:<pattern_name>:<reason>". Empty today.
# Ratchet may only SHRINK.
_ALLOWED_VIOLATIONS: frozenset[str] = frozenset()


def _iter_tab_js_files():
    """Yield (Path, relpath) for every tab_*.js under static/."""
    for path in STATIC_DIR.glob("tab_*.js"):
        if path.name in _THIRD_PARTY_BASENAMES:
            continue
        rel = str(path.relative_to(ROOT))
        yield path, rel


def _iter_hits(path: Path):
    """Yield (pattern_name, lineno, line_text) for every banned-pattern
    hit in ``path``."""
    text = path.read_text(encoding="utf-8")
    for name, regex, _reason in _BANNED_PATTERNS:
        for match in regex.finditer(text):
            # Compute line number by counting \n before the match.
            lineno = text[: match.start()].count("\n") + 1
            line = text.splitlines()[lineno - 1] if lineno - 1 < len(
                text.splitlines()
            ) else ""
            yield name, lineno, line.strip()


class XssSafeUIRatchet(unittest.TestCase):

    def test_no_banned_dom_sinks_in_tab_js(self) -> None:
        violations: list[str] = []
        for path, rel in _iter_tab_js_files():
            for name, lineno, line in _iter_hits(path):
                key = f"{rel}:{name}"
                if key in _ALLOWED_VIOLATIONS:
                    continue
                reason = next(
                    r for n, _, r in _BANNED_PATTERNS if n == name
                )
                violations.append(
                    f"{rel}:{lineno}:{name} — {reason}\n    {line}",
                )
        self.assertFalse(
            violations,
            "XSS-unsafe DOM sinks detected in dashboard tab JS:\n  - "
            + "\n  - ".join(violations),
        )

    def test_patterns_compile_and_are_unique(self) -> None:
        """Self-check: the pattern table itself stays well-formed."""
        names = [name for name, _, _ in _BANNED_PATTERNS]
        self.assertEqual(
            len(names), len(set(names)),
            "duplicate pattern name in _BANNED_PATTERNS",
        )
        for name, regex, reason in _BANNED_PATTERNS:
            self.assertIsInstance(regex, re.Pattern)
            self.assertTrue(reason, f"{name} missing reason")

    def test_allowlist_only_references_real_lines(self) -> None:
        """If an allowed violation no longer exists, the entry is stale."""
        if not _ALLOWED_VIOLATIONS:
            return
        live: set[str] = set()
        for path, rel in _iter_tab_js_files():
            for name, _lineno, _line in _iter_hits(path):
                live.add(f"{rel}:{name}")
        stale = [
            entry for entry in _ALLOWED_VIOLATIONS if entry not in live
        ]
        self.assertFalse(
            stale,
            "Stale _ALLOWED_VIOLATIONS entries — remove them:\n  - "
            + "\n  - ".join(stale),
        )


class BannedPatternUnitTests(unittest.TestCase):
    """Regex self-tests — each pattern catches the canonical bad
    example and passes the canonical safe example."""

    def _match(self, pattern_name: str, text: str) -> bool:
        regex = next(
            r for n, r, _ in _BANNED_PATTERNS if n == pattern_name
        )
        return bool(regex.search(text))

    def test_innerHTML_assignment(self) -> None:
        self.assertTrue(self._match(
            "innerHTML_assignment", "el.innerHTML = userInput;",
        ))
        self.assertFalse(self._match(
            "innerHTML_assignment", "const x = el.innerHTML;",
        ))

    def test_outerHTML_assignment(self) -> None:
        self.assertTrue(self._match(
            "outerHTML_assignment", "node.outerHTML = '<b>x</b>';",
        ))
        self.assertFalse(self._match(
            "outerHTML_assignment", "console.log(node.outerHTML);",
        ))

    def test_document_write(self) -> None:
        self.assertTrue(self._match(
            "document_write", "document.write('<script></script>');",
        ))
        self.assertTrue(self._match(
            "document_write", "document.writeln('x');",
        ))
        self.assertFalse(self._match(
            "document_write", "document.location.href;",
        ))

    def test_eval_call(self) -> None:
        self.assertTrue(self._match("eval_call", "eval(input);"))
        # "e" at the start of a word — not eval
        self.assertFalse(self._match("eval_call", "reeval(input);"))

    def test_new_function(self) -> None:
        self.assertTrue(self._match(
            "new_function", "const f = new Function('return 1');",
        ))

    def test_setTimeout_string(self) -> None:
        self.assertTrue(self._match(
            "setTimeout_string", "setTimeout('run()', 100);",
        ))
        self.assertFalse(self._match(
            "setTimeout_string", "setTimeout(run, 100);",
        ))

    def test_setInterval_string(self) -> None:
        self.assertTrue(self._match(
            "setInterval_string", "setInterval(\"tick()\", 500);",
        ))
        self.assertFalse(self._match(
            "setInterval_string", "setInterval(tick, 500);",
        ))

    def test_insertAdjacentHTML(self) -> None:
        self.assertTrue(self._match(
            "insertAdjacentHTML",
            "el.insertAdjacentHTML('beforeend', html);",
        ))


if __name__ == "__main__":
    unittest.main()
