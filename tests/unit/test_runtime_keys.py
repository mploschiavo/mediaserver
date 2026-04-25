"""Tests for media_stack.api.services.runtime_keys.

Coverage:
- env var precedence over file
- file fallback when env empty
- ``replace-after-first-boot`` placeholder treated as not set
- ``None`` return when neither source has a key
- 30s cache (second call doesn't re-read disk)
- ``invalidate_cache`` clears the entry
- ``services_missing_keys`` returns the right list
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services import runtime_keys as rk  # noqa: E402


class _TmpEnv:
    """Tiny ``patch.dict``-style helper that always restores."""

    def __init__(self, **vars_: str) -> None:
        self._vars = vars_
        self._prev: dict[str, str | None] = {}

    def __enter__(self) -> "_TmpEnv":
        for k, v in self._vars.items():
            self._prev[k] = os.environ.get(k)
            os.environ[k] = v
        return self

    def __exit__(self, *_: object) -> None:
        for k, prev in self._prev.items():
            if prev is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = prev


class RuntimeKeysTests(unittest.TestCase):
    def setUp(self) -> None:
        rk.invalidate_cache()

    def test_env_var_wins_over_file(self) -> None:
        with _TmpEnv(SONARR_API_KEY="env-value"), patch.object(
            rk, "_read_file_key", return_value="file-value",
        ) as file_mock:
            self.assertEqual(rk.read_service_api_key("sonarr"), "env-value")
            file_mock.assert_not_called()

    def test_file_fallback_when_env_empty(self) -> None:
        with _TmpEnv(SONARR_API_KEY=""), patch.object(
            rk, "_read_file_key", return_value="file-value",
        ):
            self.assertEqual(rk.read_service_api_key("sonarr"), "file-value")

    def test_returns_none_when_neither_source_has_key(self) -> None:
        with _TmpEnv(SONARR_API_KEY=""), patch.object(
            rk, "_read_file_key", return_value="",
        ):
            self.assertIsNone(rk.read_service_api_key("sonarr"))

    def test_replace_placeholder_treated_as_unset(self) -> None:
        with _TmpEnv(SONARR_API_KEY="replace-after-first-boot"), patch.object(
            rk, "_read_file_key", return_value="real-key",
        ):
            self.assertEqual(rk.read_service_api_key("sonarr"), "real-key")

    def test_cache_avoids_repeated_disk_reads(self) -> None:
        with _TmpEnv(SONARR_API_KEY=""), patch.object(
            rk, "_read_file_key", return_value="cached-value",
        ) as file_mock:
            rk.read_service_api_key("sonarr")
            rk.read_service_api_key("sonarr")
            rk.read_service_api_key("sonarr")
            # Only one disk read despite three calls
            self.assertEqual(file_mock.call_count, 1)

    def test_invalidate_cache_forces_refresh(self) -> None:
        with _TmpEnv(SONARR_API_KEY=""), patch.object(
            rk, "_read_file_key", return_value="v1",
        ) as file_mock:
            rk.read_service_api_key("sonarr")
            rk.invalidate_cache("sonarr")
            file_mock.return_value = "v2"
            self.assertEqual(rk.read_service_api_key("sonarr"), "v2")
            self.assertEqual(file_mock.call_count, 2)

    def test_invalidate_all_clears_every_entry(self) -> None:
        with _TmpEnv(SONARR_API_KEY="", RADARR_API_KEY=""), patch.object(
            rk, "_read_file_key", return_value="x",
        ) as file_mock:
            rk.read_service_api_key("sonarr")
            rk.read_service_api_key("radarr")
            self.assertEqual(file_mock.call_count, 2)
            rk.invalidate_cache()
            rk.read_service_api_key("sonarr")
            rk.read_service_api_key("radarr")
            self.assertEqual(file_mock.call_count, 4)

    def test_cache_remembers_none_result(self) -> None:
        """Caching the *absence* of a key avoids re-faulting disk on
        every render — important when the dashboard hits 8 endpoints
        per refresh."""
        with _TmpEnv(SONARR_API_KEY=""), patch.object(
            rk, "_read_file_key", return_value="",
        ) as file_mock:
            self.assertIsNone(rk.read_service_api_key("sonarr"))
            self.assertIsNone(rk.read_service_api_key("sonarr"))
            self.assertEqual(file_mock.call_count, 1)

    def test_empty_service_id_returns_none(self) -> None:
        self.assertIsNone(rk.read_service_api_key(""))


class ServicesMissingKeysTests(unittest.TestCase):
    def setUp(self) -> None:
        rk.invalidate_cache()

    def test_returns_only_services_with_no_key(self) -> None:
        from media_stack.api.services.registry import ServiceDef

        fake_services = [
            ServiceDef(id="sonarr", name="Sonarr", api_key_env="SONARR_API_KEY"),
            ServiceDef(id="radarr", name="Radarr", api_key_env="RADARR_API_KEY"),
            ServiceDef(id="bazarr", name="Bazarr", api_key_env=""),  # not in scope
        ]
        with patch("media_stack.api.services.registry.SERVICES", fake_services), \
             patch("media_stack.api.services.registry.is_service_enabled", return_value=True), \
             patch.object(rk, "read_service_api_key", side_effect=lambda s: "k" if s == "sonarr" else None):
            missing = rk.services_missing_keys()
        self.assertEqual(missing, ["radarr"])

    def test_skips_services_without_api_key_env(self) -> None:
        """Services that don't declare ``api_key_env`` (e.g. webless
        helpers) shouldn't show up as missing — they legitimately have
        no credential to discover."""
        from media_stack.api.services.registry import ServiceDef

        fake_services = [
            ServiceDef(id="bazarr", name="Bazarr", api_key_env=""),
        ]
        with patch("media_stack.api.services.registry.SERVICES", fake_services):
            self.assertEqual(rk.services_missing_keys(), [])


if __name__ == "__main__":
    unittest.main()
