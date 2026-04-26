"""Tests for the configure-indexers job."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.prowlarr.configure_indexers_job import (  # noqa: E402
    configure_indexers,
    _build_arr_apps,
)


def _ctx(
    *,
    prowlarr_url: str = "http://prowlarr:9696",
    prowlarr_key: str = "key-prowlarr",
    cfg: dict | None = None,
    api_keys: dict[str, str] | None = None,
    urls: dict[str, str] | None = None,
    admin_password: str = "pw",
    wait_timeout: int = 30,
):
    api_keys = api_keys or {}
    urls = urls or {}
    return SimpleNamespace(
        cfg=cfg or {},
        wait_timeout=wait_timeout,
        admin_username="admin",
        admin_password=admin_password,
        service_url=lambda sid: prowlarr_url if sid == "prowlarr" else urls.get(sid, ""),
        api_key=lambda sid: prowlarr_key if sid == "prowlarr" else api_keys.get(sid, ""),
    )


class ConfigureIndexersJobTests(unittest.TestCase):
    def test_skipped_when_prowlarr_missing(self):
        ctx = _ctx(prowlarr_url="", prowlarr_key="")
        result = configure_indexers(ctx)
        self.assertIn("skipped", result)

    def test_invokes_pipeline_and_returns_counts(self):
        ctx = _ctx(
            cfg={"prowlarr": {"indexers": [{"name": "A"}, {"name": "B"}], "auto_indexers": True}},
            api_keys={"sonarr": "k1"},
            urls={"sonarr": "http://sonarr:8989"},
        )
        with patch(
            "media_stack.services.apps.prowlarr.configure_indexers_job._build_arr_apps",
            return_value=[{"name": "Sonarr", "api_key": "k1", "url": "http://sonarr:8989",
                           "implementation": "Sonarr", "app_name": "Sonarr"}],
        ), patch(
            "media_stack.services.apps.prowlarr.runtime_ops.run_prowlarr_indexer_pipeline",
            return_value={"added": 2},
        ):
            result = configure_indexers(ctx)
        self.assertEqual(result.get("arr_apps"), 1)
        self.assertEqual(result.get("indexers"), 2)
        self.assertEqual(result.get("result"), {"added": 2})

    def test_error_wrapped(self):
        ctx = _ctx()
        with patch(
            "media_stack.services.apps.prowlarr.configure_indexers_job._build_arr_apps",
            return_value=[],
        ), patch(
            "media_stack.services.apps.prowlarr.runtime_ops.run_prowlarr_indexer_pipeline",
            side_effect=RuntimeError("pipeline exploded"),
        ):
            result = configure_indexers(ctx)
        self.assertIn("error", result)
        self.assertIn("pipeline exploded", result["error"])


if __name__ == "__main__":
    unittest.main()
