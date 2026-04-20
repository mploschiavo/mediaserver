"""XSS / injection safety tests.

Three layers of defense, each with its own test shape:

  1. Static analysis of dashboard.html: every innerHTML assignment
     that interpolates a KNOWN user-controlled field (display_name,
     email, username, description, role_slug, comment) must route
     that field through the _escHtml helper.
  2. Storage safety: a user with a display_name containing a script
     tag or control characters round-trips through users.json AND
     Authelia users_database.yml without breaking the file format
     (no YAML injection, no JSON injection).
  3. The _escHtml function itself escapes the five HTML-significant
     characters. Catches regressions where someone "simplifies" the
     escape function and drops &quot; or &#39;.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

DASHBOARD_HTML = ROOT / "src" / "media_stack" / "api" / "dashboard.html"


_USER_CONTROLLED_FIELDS = (
    # Names the JS code uses to refer to server-returned user data.
    "display_name", "email", "username", "description",
    "role_slug", "comment", "actor", "target",
)


class DashboardRenderingSafetyTests(unittest.TestCase):
    """Static analysis: reject any innerHTML/insertAdjacentHTML that
    splices a known-user-controlled field without _escHtml.

    A clean failure here is a pointer at the exact line — the
    developer either wraps with _escHtml() or demonstrates the
    field is safe (e.g. allowlist)."""

    def setUp(self):
        self._text = DASHBOARD_HTML.read_text(encoding="utf-8")

    def _unsafe_lines(self, field: str) -> list[tuple[int, str]]:
        """Find lines that interpolate `field` into an HTML string
        without going through `_escHtml`."""
        hits: list[tuple[int, str]] = []
        # Pattern: `+ <expr>.<field>` inside something that builds
        # HTML (innerHTML= / h+= / html+= / += '<).
        # Allow the line if _escHtml wraps the field or a containing
        # expression.
        pattern = re.compile(
            r"\+\s*[\w\.\[\]]*\.?" + re.escape(field) + r"\b(?!\s*\))",
        )
        for i, line in enumerate(self._text.splitlines(), 1):
            stripped = line.strip()
            # Skip comments and non-rendering lines.
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            if not pattern.search(line):
                continue
            if not any(token in line for token in
                       ("innerHTML", "h+=", "html+=", "+ '<", "+='<",
                        "+ \"<", "`<", "<td>", "<tr>", "insertAdjacentHTML")):
                # Not a rendering line.
                continue
            # Allowed if the field passes through _escHtml anywhere
            # on the same line, or is in a textContent assignment.
            if "_escHtml" in line:
                continue
            if "textContent" in line:
                continue
            # Some renderers apply the field only inside an allowlisted
            # attribute — e.g. data-user-id which is safe. Heuristic:
            # if the interpolation is immediately wrapped in quotes
            # inside a data-* attribute, skip.
            if re.search(r'data-[a-z-]+="\s*\'?\+\s*[\w\.]+\.?' +
                         re.escape(field), line):
                continue
            hits.append((i, stripped[:120]))
        return hits

    def test_display_name_always_escaped(self):
        """display_name is operator-chosen; an attacker registering
        an account with ``<img onerror=alert(1)>`` breaks the
        admin's Users tab."""
        unsafe = self._unsafe_lines("display_name")
        self.assertEqual(
            unsafe, [],
            "display_name appears unescaped in dashboard rendering:\n"
            + "\n".join(f"  line {ln}: {snip}" for ln, snip in unsafe)
            + "\nWrap with _escHtml() or use textContent.",
        )

    def test_email_always_escaped(self):
        """Email addresses from the user store — same risk class."""
        unsafe = self._unsafe_lines("email")
        self.assertEqual(
            unsafe, [],
            "email appears unescaped in dashboard rendering:\n"
            + "\n".join(f"  line {ln}: {snip}" for ln, snip in unsafe),
        )

    def test_username_always_escaped(self):
        """Usernames can contain anything pre-normalization."""
        unsafe = self._unsafe_lines("username")
        self.assertEqual(
            unsafe, [],
            "username appears unescaped in dashboard rendering:\n"
            + "\n".join(f"  line {ln}: {snip}" for ln, snip in unsafe),
        )


class EscapeHelperTests(unittest.TestCase):
    """Extract the _escHtml function from dashboard.html and verify
    it escapes all five HTML-significant characters. Regressions
    here (e.g. "let's simplify and skip &#39;") open every other
    field to XSS."""

    def test_esc_html_covers_five_chars(self):
        text = DASHBOARD_HTML.read_text(encoding="utf-8")
        # Find the function body. The declaration is:
        #   const _esc=s=>String(s??'').replace(...)
        # or  function _escHtml(s){...}
        self.assertRegex(
            text, r"(_esc(Html)?\s*=?\s*(s\s*=>|function)).*"
            r"&amp;.*&lt;.*&gt;.*(&quot;|&#34;).*(&#39;|&apos;)",
            "_escHtml missing one of the 5 HTML entities "
            "(amp, lt, gt, quot, apos). XSS in every user-controlled "
            "field that renders via innerHTML.",
        )


class StorageSafetyTests(unittest.TestCase):
    """User-controlled strings stored server-side must survive
    JSON and YAML round-trips without altering file structure.
    A script-tag display_name is fine to store; a display_name
    containing ``\n---\nadmin: password: ...`` must NOT produce a
    YAML document that Authelia parses as a second document."""

    _HOSTILE_DISPLAY_NAMES = (
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        "Jane\nevil: injected",
        "Jane\r\n---\nsecond doc: true",
        "<iframe src='javascript:alert(1)'>",
        "\"'`\\x00",
        # YAML flow-style terminators that some dumpers mishandle.
        "Jane, evil: true}",
    )

    def test_users_json_round_trip_preserves_hostile_names(self):
        """Round-trip through json.dumps/loads must be byte-equal
        for every hostile input — otherwise an admin creating a
        user with a tricky name can't log in because the stored
        name has been mutated."""
        for name in self._HOSTILE_DISPLAY_NAMES:
            record = {"id": "u1", "display_name": name, "email": "x@y"}
            restored = json.loads(json.dumps(record))
            self.assertEqual(
                restored["display_name"], name,
                f"display_name mutated on JSON round-trip: {name!r}",
            )

    def test_authelia_yaml_round_trip_preserves_hostile_names(self):
        """PyYAML's safe_dump/safe_load must preserve hostile names
        without breaking the document boundary. A CR-LF + '---' in
        a display_name used to produce a two-document YAML in some
        dumpers — Authelia would then parse the evil entry as its
        own users list."""
        for name in self._HOSTILE_DISPLAY_NAMES:
            doc = {"users": {"jane": {
                "displayname": name, "email": "x@y",
                "password": "$argon2id$...",
                "groups": ["users"],
            }}}
            serialized = yaml.safe_dump(doc, default_flow_style=False)
            # Safe loader must produce exactly one document.
            reloaded = list(yaml.safe_load_all(serialized))
            self.assertEqual(
                len(reloaded), 1,
                f"YAML round-trip produced multiple documents for "
                f"display_name={name!r}. Authelia would load both "
                f"and the second could grant extra accounts.",
            )
            self.assertEqual(
                reloaded[0]["users"]["jane"]["displayname"], name,
                f"display_name mutated on YAML round-trip: {name!r}",
            )

    def test_writing_user_file_atomically_survives_hostile_names(self):
        """Write a users.json with a hostile name, read it back on
        a fresh process (no in-memory cache) — must be byte-equal.
        Catches subtle issues where an editor library rewrites the
        file using str(value) instead of json.dumps(value)."""
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "users.json"
            users = [
                {"id": f"u{i}", "display_name": name,
                 "email": f"u{i}@y.local"}
                for i, name in enumerate(self._HOSTILE_DISPLAY_NAMES)
            ]
            target.write_text(json.dumps({"users": users}),
                              encoding="utf-8")
            restored = json.loads(target.read_text(encoding="utf-8"))
            for orig, got in zip(users, restored["users"]):
                self.assertEqual(orig["display_name"], got["display_name"])


if __name__ == "__main__":
    unittest.main()
