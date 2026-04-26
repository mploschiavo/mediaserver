"""Tests for PasswordPolicy — strength + history check."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.users.password_policy import PasswordPolicy  # noqa: E402


class StrengthTests(unittest.TestCase):
    def _policy(self, **kw):
        return PasswordPolicy(history_salt="test-salt", **kw)

    def test_short_password_rejected(self):
        p = self._policy(min_length=12)
        r = p.check_candidate("Aa1!aaa")
        self.assertFalse(r.ok)
        self.assertIn("too short", r.reason)

    def test_common_password_rejected(self):
        p = self._policy(min_length=6, require_class_count=1)
        r = p.check_candidate("password")
        self.assertFalse(r.ok)
        self.assertIn("too common", r.reason)

    def test_insufficient_classes_rejected(self):
        p = self._policy(require_class_count=3)
        r = p.check_candidate("aaaaaaaaaaaa")
        self.assertFalse(r.ok)
        self.assertIn("character classes", r.reason)

    def test_strong_password_accepted(self):
        p = self._policy()
        self.assertTrue(p.check_candidate("Strong_Pass-2026!").ok)

    def test_class_count_symbols(self):
        p = self._policy(min_length=4, require_class_count=1)
        self.assertTrue(p.check_candidate("!!!!").ok)


class HistoryTests(unittest.TestCase):
    def test_reused_password_rejected(self):
        p = PasswordPolicy(history_salt="s")
        first_hashes = p.push_history([], "Strong_Pass-2026!")
        r = p.check_candidate("Strong_Pass-2026!", history_hashes=first_hashes)
        self.assertFalse(r.ok)
        self.assertIn("reused", r.reason)

    def test_new_password_outside_history_accepted(self):
        p = PasswordPolicy(history_salt="s")
        hashes = p.push_history([], "Strong_Pass-2026!")
        r = p.check_candidate("Other_Str0ng-Pass!", history_hashes=hashes)
        self.assertTrue(r.ok)

    def test_history_truncates_to_limit(self):
        p = PasswordPolicy(history_salt="s", history_len=3)
        hashes: list[str] = []
        for pw in ("Pass1_Abc-Xyz", "Pass2_Abc-Xyz", "Pass3_Abc-Xyz",
                   "Pass4_Abc-Xyz", "Pass5_Abc-Xyz"):
            hashes = p.push_history(hashes, pw)
        self.assertEqual(len(hashes), 3)
        # Most recent at front; oldest dropped
        self.assertTrue(p.check_candidate("Pass1_Abc-Xyz",
                                           history_hashes=hashes).ok)
        self.assertFalse(p.check_candidate("Pass5_Abc-Xyz",
                                            history_hashes=hashes).ok)

    def test_zero_history_accepts_everything(self):
        p = PasswordPolicy(history_salt="s", history_len=0)
        hashes = p.push_history([], "Any_Pass-2026!")
        self.assertEqual(hashes, [])

    def test_different_salt_gives_different_hash(self):
        p1 = PasswordPolicy(history_salt="a")
        p2 = PasswordPolicy(history_salt="b")
        h1 = p1.push_history([], "Secret_Pass-Xyz!")
        h2 = p2.push_history([], "Secret_Pass-Xyz!")
        self.assertNotEqual(h1, h2)


if __name__ == "__main__":
    unittest.main()
