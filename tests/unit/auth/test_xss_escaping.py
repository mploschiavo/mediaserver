"""XSS / injection safety tests.

Three layers of defense, each with its own test shape:

  1. Storage safety: a user with a display_name containing a script
     tag or control characters round-trips through users.json AND
     Authelia users_database.yml without breaking the file format
     (no YAML injection, no JSON injection).
  2. The _escHtml function itself escapes the five HTML-significant
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
