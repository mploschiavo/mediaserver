"""Tests for the CSRF protector (double-submit cookie)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.csrf import CsrfProtector  # noqa: E402


class CsrfProtectorTests(unittest.TestCase):
    def test_issue_token_is_unique_and_nonempty(self):
        p = CsrfProtector()
        a = p.issue_token()
        b = p.issue_token()
        self.assertTrue(a)
        self.assertTrue(b)
        self.assertNotEqual(a, b)

    def test_verify_happy_path(self):
        p = CsrfProtector()
        token = p.issue_token()
        cookie = f"foo=bar; {p.cookie_name}={token}; other=baz"
        self.assertTrue(p.verify(cookie_header=cookie, header_value=token))

    def test_verify_rejects_mismatch(self):
        p = CsrfProtector()
        cookie = f"{p.cookie_name}=aaaa"
        self.assertFalse(p.verify(cookie_header=cookie, header_value="bbbb"))

    def test_verify_rejects_missing_cookie(self):
        p = CsrfProtector()
        self.assertFalse(p.verify(cookie_header="", header_value="x"))

    def test_verify_rejects_missing_header(self):
        p = CsrfProtector()
        cookie = f"{p.cookie_name}=x"
        self.assertFalse(p.verify(cookie_header=cookie, header_value=""))

    def test_mutating_method_detection(self):
        p = CsrfProtector()
        for m in ("POST", "PUT", "DELETE", "PATCH", "post"):
            self.assertTrue(p.is_mutating_method(m), m)
        for m in ("GET", "HEAD", "OPTIONS", ""):
            self.assertFalse(p.is_mutating_method(m), m)

    def test_set_cookie_has_samesite_strict(self):
        p = CsrfProtector()
        cookie_str = p.build_set_cookie("abc", secure=False)
        self.assertIn("SameSite=Strict", cookie_str)
        self.assertIn("Path=/", cookie_str)
        self.assertNotIn("Secure", cookie_str)

    def test_set_cookie_with_secure(self):
        p = CsrfProtector()
        self.assertIn("Secure", p.build_set_cookie("abc", secure=True))


if __name__ == "__main__":
    unittest.main()
