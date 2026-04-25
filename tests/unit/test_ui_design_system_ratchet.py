"""Ratchet: dashboard.html + tab_*.js conform to the UI design system.

Why a ratchet
-------------
The dashboard grew tab-by-tab with no shared visual contract. Each
new tab invented its own classes (``ms-mi-*``, ``ms-security-*``,
``ms-chip-overflow``, ``ms-mi-warning``…) and reached for inline
``style="…"`` attributes when CSS classes felt heavy. The result is
8 tabs that look like 8 different products.

``docs/ui-design-system.md`` is the contract. This ratchet enforces
it. Today the dashboard has hundreds of inline styles and dozens of
non-system class names. Each migration PR drops a count here. The
ratchet:

* ALLOWS the current numbers (the floor we're starting from).
* FAILS if any number INCREASES (regression).
* FAILS if any number DECREASES without updating this file
  (forces the PR author to commit the win and have it reviewed).

When you migrate a tab, you'll edit ``EXPECTED_VIOLATIONS`` to lower
the count for that file. That edit goes in the same PR as the
migration. Reviewers can see the size of the win in the diff.

Three counters
--------------
1. **Inline-style attributes** in ``dashboard.html`` HTML elements:
   ``<div style="…">``. Each occurrence is one violation.
2. **Inline-style string literals** in JS files (template strings or
   concatenations that include ``style="…"``). Each match is one
   violation.
3. **Non-system CSS class names** referenced in ``class="…"``
   attributes. Anything not in ``ALLOWED_CLASSES`` counts as one
   violation per occurrence.

If you genuinely need a new class, add it to ``ALLOWED_CLASSES`` AND
to ``docs/ui-design-system.md``. If you genuinely need an inline
style (dynamic width, runtime color), update the
``INLINE_STYLE_ALLOWLIST`` patterns. Drift gets reviewed.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]
_DASHBOARD = _ROOT / "src" / "media_stack" / "api" / "dashboard.html"
_STATIC_DIR = _ROOT / "src" / "media_stack" / "api" / "static"


# ---------------------------------------------------------------------------
# Allowed classes — the canonical design-system vocabulary.
# Add a new class here ONLY after documenting it in
# docs/ui-design-system.md. Removing a class is fine if no source
# references it.
# ---------------------------------------------------------------------------

ALLOWED_CLASSES: frozenset[str] = frozenset({
    # --- structural / layout ---
    "tab-bar", "tab-content", "tab-description",
    "sub-tab-bar", "subtab",
    "card", "card-body", "card-section-title",
    "action-bar",
    "kv-grid",
    "page-banner", "page-banner-warning", "page-banner-error",
    "main-tabs",

    # --- buttons ---
    "btn-primary", "btn-secondary", "btn-danger",
    "btn-compact",
    "active",

    # --- chips / badges ---
    "chip", "chip-success", "chip-warning", "chip-error", "chip-info",

    # --- forms ---
    "form-row", "form-row-checkbox",
    "form-label", "form-input", "form-select", "form-checkbox",
    "form-textarea",

    # --- tables ---
    "ms-table",

    # --- status messaging ---
    "state-msg", "state-msg-alert",

    # --- meta / utility (rare, documented exceptions) ---
    "collapsed", "collapse-icon",
    "hidden",
    "tab-bar",  # legacy; tab buttons live in <div class="tab-bar">
    "settings-row",
    "mobile-bar",
    "filter-bar",
    "svc-sort",
    "tooltip",
    "tooltip-trigger",

    # --- branding (logo + footer; out of design-system scope) ---
    "logo", "wordmark", "icon",
    "footer",
})


# Inline-style patterns that are allowed (regex matched against the
# full ``style="…"`` value). Anything not matching a pattern in this
# list counts as a violation. Keep this list short — it's the
# "structurally dynamic" carve-out.
INLINE_STYLE_ALLOWLIST: tuple[re.Pattern[str], ...] = (
    # JS-driven progress-bar width: style="width:42%" computed at
    # runtime from a number. CSS handles the rest of the bar.
    re.compile(r"^\s*width\s*:\s*[\d.]+%?\s*;?\s*$"),
)


# ---------------------------------------------------------------------------
# Violation budget. EVERY entry is a non-zero starting point. To
# migrate a tab, lower the corresponding number in the same PR as
# the migration. If your migration drops the count to 0, leave the
# entry in place with value 0 — the ratchet will protect against
# regression.
# ---------------------------------------------------------------------------

EXPECTED_VIOLATIONS: dict[str, dict[str, int]] = {
    "dashboard.html": {
        "inline_styles": 0,        # placeholder — calibrated below
        "non_system_classes": 0,   # placeholder — calibrated below
    },
    "tab_bans.js": {
        "inline_strings_with_style": 0,
        "non_system_classes": 0,
    },
    "tab_emergency_revoke.js": {
        "inline_strings_with_style": 0,
        "non_system_classes": 0,
    },
    "tab_me_security.js": {
        "inline_strings_with_style": 0,
        "non_system_classes": 0,
    },
    "tab_media_integrity.js": {
        "inline_strings_with_style": 0,
        "non_system_classes": 0,
    },
    "tab_security.js": {
        "inline_strings_with_style": 0,
        "non_system_classes": 0,
    },
    "tab_sessions.js": {
        "inline_strings_with_style": 0,
        "non_system_classes": 0,
    },
}


# ---------------------------------------------------------------------------
# Scanners
# ---------------------------------------------------------------------------


_HTML_STYLE_ATTR = re.compile(r'\bstyle\s*=\s*"([^"]*)"')
_HTML_CLASS_ATTR = re.compile(r'\bclass\s*=\s*"([^"]*)"')
_JS_INLINE_STYLE_STRING = re.compile(r'(?:\'|")[^\'"]*style\s*=')


def _classes_in_text(text: str) -> list[str]:
    """Yield every class token referenced in ``class="…"`` attrs."""
    out: list[str] = []
    for match in _HTML_CLASS_ATTR.finditer(text):
        for tok in match.group(1).split():
            tok = tok.strip()
            if tok:
                out.append(tok)
    return out


def _inline_styles_in_text(text: str) -> list[str]:
    """Yield every disallowed ``style="…"`` value in HTML."""
    out: list[str] = []
    for match in _HTML_STYLE_ATTR.finditer(text):
        value = match.group(1)
        if any(p.match(value) for p in INLINE_STYLE_ALLOWLIST):
            continue
        out.append(value)
    return out


def _inline_style_strings_in_js(text: str) -> list[str]:
    """Count JS string literals that contain ``style="…"`` —
    ad-hoc styling injected via innerHTML or template strings."""
    return _JS_INLINE_STYLE_STRING.findall(text)


def _non_system_classes(text: str) -> list[str]:
    """Classes not in the canonical vocabulary."""
    return [c for c in _classes_in_text(text) if c not in ALLOWED_CLASSES]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class UiDesignSystemRatchet(unittest.TestCase):

    def test_dashboard_html_exists(self) -> None:
        self.assertTrue(
            _DASHBOARD.is_file(),
            f"dashboard.html missing at {_DASHBOARD}",
        )

    def test_dashboard_inline_style_budget(self) -> None:
        text = _DASHBOARD.read_text(encoding="utf-8")
        violations = _inline_styles_in_text(text)
        actual = len(violations)
        expected = EXPECTED_VIOLATIONS["dashboard.html"]["inline_styles"]
        if actual != expected:
            sample = "\n  ".join(sorted(set(violations))[:10])
            self.fail(self._budget_message(
                "dashboard.html", "inline_styles", actual, expected,
                sample=sample,
            ))

    def test_dashboard_class_vocabulary_budget(self) -> None:
        text = _DASHBOARD.read_text(encoding="utf-8")
        violations = _non_system_classes(text)
        actual = len(violations)
        expected = EXPECTED_VIOLATIONS["dashboard.html"]["non_system_classes"]
        if actual != expected:
            unique = sorted(set(violations))
            self.fail(self._budget_message(
                "dashboard.html", "non_system_classes", actual, expected,
                sample="\n  ".join(unique[:15]),
            ))

    def test_tab_js_files_under_budget(self) -> None:
        """Each tab_*.js file is held to two budgets:
        ``inline_strings_with_style`` and ``non_system_classes``."""
        problems: list[str] = []
        for js_file in sorted(_STATIC_DIR.glob("tab_*.js")):
            text = js_file.read_text(encoding="utf-8")
            entry = EXPECTED_VIOLATIONS.get(js_file.name)
            if entry is None:
                problems.append(
                    f"\n{js_file.name}: not in EXPECTED_VIOLATIONS. "
                    f"Add an entry with current counts so future "
                    f"changes are budgeted.",
                )
                continue
            inline_actual = len(_inline_style_strings_in_js(text))
            inline_exp = entry["inline_strings_with_style"]
            if inline_actual != inline_exp:
                problems.append(self._budget_message(
                    js_file.name, "inline_strings_with_style",
                    inline_actual, inline_exp,
                ))
            class_violations = _non_system_classes(text)
            class_actual = len(class_violations)
            class_exp = entry["non_system_classes"]
            if class_actual != class_exp:
                unique = sorted(set(class_violations))
                problems.append(self._budget_message(
                    js_file.name, "non_system_classes",
                    class_actual, class_exp,
                    sample="\n  ".join(unique[:15]),
                ))
        if problems:
            self.fail("\n\n".join(problems))

    def test_design_system_doc_exists(self) -> None:
        doc = _ROOT / "docs" / "ui-design-system.md"
        self.assertTrue(
            doc.is_file(),
            f"docs/ui-design-system.md is missing. The ratchet enforces "
            f"the contract; the doc explains it. They ship together.",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _budget_message(
        filename: str,
        metric: str,
        actual: int,
        expected: int,
        *,
        sample: str = "",
    ) -> str:
        direction = "increased" if actual > expected else "decreased"
        guidance = (
            "If you're adding a new visual pattern, justify it in "
            "docs/ui-design-system.md and update ALLOWED_CLASSES / "
            "INLINE_STYLE_ALLOWLIST in this test file accordingly."
            if direction == "increased"
            else "Migration win — update EXPECTED_VIOLATIONS in this "
                 "test file with the new lower number so the ratchet "
                 "protects the gain."
        )
        msg = (
            f"\n\n{filename}: {metric} {direction} from {expected} "
            f"to {actual}.\n\n{guidance}"
        )
        if sample:
            msg += f"\n\nSample:\n  {sample}"
        return msg


if __name__ == "__main__":
    unittest.main()
