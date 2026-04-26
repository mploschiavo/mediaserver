"""Tests for the secret-redaction helpers.

These helpers are load-bearing for **confidentiality** per the
security contract — every response body / log line that might carry
a secret flows through them. Coverage expectations are higher than
typical (95%+): a missed branch here is a real-world leak.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.secret_redaction import (  # noqa: E402
    fingerprint,
    redact_api_key_map,
    redact_if_secret_key,
    redact_url_query,
)


class FingerprintTests(unittest.TestCase):

    def test_empty_returns_empty(self) -> None:
        self.assertEqual(fingerprint(""), "")

    def test_short_under_8_fully_redacted(self) -> None:
        # Below 8 chars the edges trivially leak the full secret,
        # so the whole thing is hidden.
        self.assertEqual(fingerprint("abc"), "[redacted]")
        self.assertEqual(fingerprint("1234567"), "[redacted]")

    def test_medium_uses_smaller_window(self) -> None:
        # 8 to 11 chars get a 2+2 reveal.
        fp = fingerprint("abcdefgh")
        self.assertIn("…", fp)
        self.assertTrue(fp.startswith("ab"))
        self.assertTrue(fp.endswith("gh"))

    def test_long_uses_full_window(self) -> None:
        fp = fingerprint("abcdefghijklmnopqrstuvwxyz")
        self.assertEqual(fp, "abcd…wxyz")

    def test_exactly_12_chars_uses_full_window(self) -> None:
        # 12 is the boundary — check we're inclusive.
        fp = fingerprint("abcdefghijkl")
        self.assertEqual(fp, "abcd…ijkl")

    def test_different_secrets_yield_different_fingerprints(self) -> None:
        # Usability invariant: the admin can distinguish two keys
        # by their fingerprints when they're distinct enough.
        self.assertNotEqual(
            fingerprint("abcdef-jellyfin-zzzzzz"),
            fingerprint("qrstuv-sonarr-mmmmm"),
        )

    def test_same_secret_yields_same_fingerprint(self) -> None:
        # Usability invariant: stable across calls.
        self.assertEqual(
            fingerprint("abcdef-jellyfin-zzzzzz"),
            fingerprint("abcdef-jellyfin-zzzzzz"),
        )

    def test_fingerprint_does_not_reveal_full_secret(self) -> None:
        secret = "supersecret-api-key-1234567890"
        fp = fingerprint(secret)
        self.assertNotIn(secret, fp)
        # None of the middle chars appear.
        self.assertNotIn("secret-api", fp)


class RedactApiKeyMapTests(unittest.TestCase):

    def test_empty_input_returns_empty(self) -> None:
        self.assertEqual(redact_api_key_map({}), {})

    def test_each_service_gets_metadata_shape(self) -> None:
        out = redact_api_key_map({
            "jellyfin": "abcdefghijklmnop",
            "sonarr": "qrstuvwxyz1234567890",
        })
        self.assertEqual(set(out), {"jellyfin", "sonarr"})
        for entry in out.values():
            self.assertIn("has_key", entry)
            self.assertIn("fingerprint", entry)
            self.assertIn("source", entry)

    def test_raw_key_never_in_output(self) -> None:
        raw = "abcdefghijklmnopqrstuvwxyz"
        out = redact_api_key_map({"jellyfin": raw})
        for entry in out.values():
            for value in entry.values():
                if isinstance(value, str):
                    self.assertNotIn(raw, value)

    def test_empty_key_reports_has_key_false(self) -> None:
        out = redact_api_key_map({"sonarr": ""})
        self.assertFalse(out["sonarr"]["has_key"])
        self.assertEqual(out["sonarr"]["fingerprint"], "")

    def test_source_label_preserved(self) -> None:
        out = redact_api_key_map(
            {"sonarr": "abcdefghij"}, source="env",
        )
        self.assertEqual(out["sonarr"]["source"], "env")

    def test_fingerprints_distinct_between_services(self) -> None:
        # Ops use-case: admin sees two keys with different
        # fingerprints and knows they're different keys.
        out = redact_api_key_map({
            "jellyfin": "abc111111111xyz",
            "sonarr": "qrs222222222mnb",
        })
        self.assertNotEqual(
            out["jellyfin"]["fingerprint"],
            out["sonarr"]["fingerprint"],
        )


class RedactIfSecretKeyTests(unittest.TestCase):

    def test_simple_dict_with_secret_field(self) -> None:
        out = redact_if_secret_key({"username": "alice", "password": "hunter2"})
        self.assertEqual(out["username"], "alice")
        self.assertEqual(out["password"], "[redacted]")

    def test_nested_dict_secret_redacted(self) -> None:
        out = redact_if_secret_key({
            "user": {"name": "alice", "api_key": "abc123"},
        })
        self.assertEqual(out["user"]["api_key"], "[redacted]")
        self.assertEqual(out["user"]["name"], "alice")

    def test_list_elements_recursed(self) -> None:
        out = redact_if_secret_key([
            {"password": "x"}, {"username": "y"},
        ])
        self.assertEqual(out[0]["password"], "[redacted]")
        self.assertEqual(out[1]["username"], "y")

    def test_case_insensitive_match(self) -> None:
        out = redact_if_secret_key({
            "API_Key": "abc", "API_KEY": "def", "ApiKey": "ghi",
        })
        for v in out.values():
            self.assertEqual(v, "[redacted]")

    def test_substring_match(self) -> None:
        # jellyfin_api_key, stack_admin_password — real examples.
        out = redact_if_secret_key({
            "jellyfin_api_key": "X",
            "stack_admin_password": "Y",
        })
        self.assertEqual(out["jellyfin_api_key"], "[redacted]")
        self.assertEqual(out["stack_admin_password"], "[redacted]")

    def test_access_token_variants(self) -> None:
        for key in ("access_token", "AccessToken", "access-token"):
            out = redact_if_secret_key({key: "xyz"})
            self.assertEqual(out[key], "[redacted]")

    def test_bearer_token_redacted(self) -> None:
        out = redact_if_secret_key({"bearer_token": "eyJ...", "bearer": "abc"})
        self.assertEqual(out["bearer_token"], "[redacted]")
        self.assertEqual(out["bearer"], "[redacted]")

    def test_non_matching_keys_passthrough(self) -> None:
        out = redact_if_secret_key({
            "username": "alice", "email": "a@x", "role": "admin",
        })
        self.assertEqual(out, {
            "username": "alice", "email": "a@x", "role": "admin",
        })

    def test_non_string_keys_passthrough(self) -> None:
        # Dicts can have non-string keys; don't blow up.
        out = redact_if_secret_key({1: "a", 2: "b"})
        self.assertEqual(out, {1: "a", 2: "b"})

    def test_tuple_recursion(self) -> None:
        out = redact_if_secret_key(({"password": "x"}, "plain"))
        self.assertIsInstance(out, tuple)
        self.assertEqual(out[0]["password"], "[redacted]")
        self.assertEqual(out[1], "plain")

    def test_deeply_nested_structures(self) -> None:
        payload = {
            "level1": {
                "level2": {
                    "level3": [
                        {"api_key": "SECRET"},
                    ],
                },
            },
        }
        out = redact_if_secret_key(payload)
        self.assertEqual(
            out["level1"]["level2"]["level3"][0]["api_key"],
            "[redacted]",
        )

    def test_private_key_variants(self) -> None:
        for key in ("private_key", "client_secret", "session_token"):
            out = redact_if_secret_key({key: "x"})
            self.assertEqual(out[key], "[redacted]")


class RedactUrlQueryTests(unittest.TestCase):

    def test_empty_input(self) -> None:
        self.assertEqual(redact_url_query(""), "")

    def test_no_secret_passthrough(self) -> None:
        self.assertEqual(
            redact_url_query("https://example.com/path?foo=bar"),
            "https://example.com/path?foo=bar",
        )

    def test_api_key_redacted(self) -> None:
        out = redact_url_query("https://j/api?api_key=ABC123")
        self.assertIn("api_key=[redacted]", out)
        self.assertNotIn("ABC123", out)

    def test_case_insensitive(self) -> None:
        out = redact_url_query("?API_KEY=XYZ")
        self.assertIn("API_KEY=[redacted]", out)

    def test_multiple_secrets(self) -> None:
        out = redact_url_query("?api_key=A&access_token=B&password=C")
        for secret in ("A", "B", "C"):
            self.assertNotIn(f"={secret}", out)

    def test_apikey_variant(self) -> None:
        out = redact_url_query("?apikey=XYZ")
        self.assertIn("apikey=[redacted]", out)
        self.assertNotIn("XYZ", out)

    def test_hyphen_variant(self) -> None:
        out = redact_url_query("?api-key=XYZ")
        self.assertIn("api-key=[redacted]", out)

    def test_preserves_non_secret_params(self) -> None:
        out = redact_url_query(
            "https://h/p?api_key=X&page=2&limit=10",
        )
        self.assertIn("page=2", out)
        self.assertIn("limit=10", out)


if __name__ == "__main__":
    unittest.main()
