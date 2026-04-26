"""Tests for the configure-arr-clients job."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.servarr.configure_arr_clients_job import (  # noqa: E402
    configure_arr_clients,
)


def _ctx(cfg=None, urls=None, keys=None):
    urls = urls or {}
    keys = keys or {}
    return SimpleNamespace(
        cfg=cfg or {},
        admin_username="admin",
        admin_password="pw",
        service_url=lambda sid: urls.get(sid, ""),
        api_key=lambda sid: keys.get(sid, ""),
    )


class ConfigureArrClientsJobTests(unittest.TestCase):
    def test_skipped_when_no_arr_services(self):
        ctx = _ctx()
        with patch(
            "media_stack.services.apps.servarr.configure_arr_clients_job._arr_services",
            return_value=[],
        ):
            result = configure_arr_clients(ctx)
        self.assertIn("skipped", result)

    def test_attaches_clients_to_each_arr(self):
        ctx = _ctx(
            cfg={"qbittorrent": {}, "sabnzbd": {}},
            urls={"sonarr": "http://sonarr:8989", "radarr": "http://radarr:7878",
                  "qbittorrent": "http://qbit:8080", "sabnzbd": "http://sab:8080"},
            keys={"sonarr": "k1", "radarr": "k2", "sabnzbd": "sk"},
        )
        arr = [
            SimpleNamespace(id="sonarr", name="Sonarr"),
            SimpleNamespace(id="radarr", name="Radarr"),
        ]
        calls = []

        def _ensure(app_payload, app_url, api_base, api_key, client_cfg, client_auth):
            calls.append((app_payload["name"], client_cfg.get("url"), client_auth.get("api_key")))

        with patch(
            "media_stack.services.apps.servarr.configure_arr_clients_job._arr_services",
            return_value=arr,
        ), patch(
            "media_stack.services.apps.servarr.runtime.arr_ops.detect_arr_api_base",
            return_value="/api/v3",
        ), patch(
            "media_stack.services.apps.servarr.runtime.arr_ops.ensure_arr_download_client",
            side_effect=_ensure,
        ):
            result = configure_arr_clients(ctx)

        self.assertIn("sonarr", result.get("configured", []))
        self.assertIn("radarr", result.get("configured", []))
        # Two calls per arr: qBit + SAB = 4 total
        self.assertEqual(len(calls), 4)

    def test_skips_arr_without_key_or_url(self):
        ctx = _ctx(urls={"qbittorrent": "http://qbit:8080"}, keys={})
        arr = [SimpleNamespace(id="sonarr", name="Sonarr")]
        with patch(
            "media_stack.services.apps.servarr.configure_arr_clients_job._arr_services",
            return_value=arr,
        ), patch(
            "media_stack.services.apps.servarr.runtime.arr_ops.detect_arr_api_base",
            return_value="/api/v3",
        ), patch(
            "media_stack.services.apps.servarr.runtime.arr_ops.ensure_arr_download_client",
        ) as m_ensure:
            result = configure_arr_clients(ctx)
        self.assertEqual(result.get("configured"), [])
        m_ensure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
