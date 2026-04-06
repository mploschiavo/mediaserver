import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.adapters.common import (  # noqa: E402
    bool_cfg,
    coerce_list,
    env_truthy,
    normalize_base_path,
    normalize_url,
    parse_service_url,
    to_int,
)


class BootstrapCommonTests(unittest.TestCase):
    def test_normalize_url(self):
        self.assertEqual(normalize_url("http://example.local/"), "http://example.local")
        self.assertEqual(normalize_url("http://example.local"), "http://example.local")

    def test_normalize_base_path(self):
        self.assertEqual(normalize_base_path(""), "")
        self.assertEqual(normalize_base_path("/"), "")
        self.assertEqual(normalize_base_path("api"), "/api")
        self.assertEqual(normalize_base_path("/api/"), "/api")

    def test_parse_service_url(self):
        parsed = parse_service_url("https://sonarr.local:8989/base/", 8989)
        self.assertEqual(parsed["hostname"], "sonarr.local")
        self.assertEqual(parsed["port"], 8989)
        self.assertTrue(parsed["use_ssl"])
        self.assertEqual(parsed["base_url"], "/base")

    def test_to_int_and_coerce_list(self):
        self.assertEqual(to_int("42"), 42)
        self.assertEqual(to_int("x", fallback=7), 7)
        self.assertEqual(coerce_list(None), [])
        self.assertEqual(coerce_list("one"), ["one"])
        self.assertEqual(coerce_list(["one", "two"]), ["one", "two"])

    def test_bool_cfg(self):
        self.assertTrue(bool_cfg({"enabled": True}, "enabled", False))
        self.assertFalse(bool_cfg({"enabled": False}, "enabled", True))
        self.assertTrue(bool_cfg({}, "enabled", True))

    def test_env_truthy(self):
        os.environ["BOOTSTRAP_TEST_TRUTHY"] = "true"
        self.assertTrue(env_truthy("BOOTSTRAP_TEST_TRUTHY", False))
        os.environ["BOOTSTRAP_TEST_TRUTHY"] = "0"
        self.assertFalse(env_truthy("BOOTSTRAP_TEST_TRUTHY", True))
        del os.environ["BOOTSTRAP_TEST_TRUTHY"]
        self.assertTrue(env_truthy("BOOTSTRAP_TEST_TRUTHY", True))


if __name__ == "__main__":
    unittest.main()
