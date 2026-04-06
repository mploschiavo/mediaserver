import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.adapters.servarr import (  # noqa: E402
    choose_profile,
    choose_root_folder,
    find_existing_servarr,
    normalize_remote_path_mappings,
)


class BootstrapServarrTests(unittest.TestCase):
    def test_choose_profile_prefers_id(self):
        profiles = [{"id": 1, "name": "Any"}, {"id": 2, "name": "HD"}]
        self.assertEqual(choose_profile(profiles, preferred_id=2)["name"], "HD")
        self.assertEqual(choose_profile(profiles, preferred_id=9)["name"], "Any")

    def test_choose_profile_prefers_names(self):
        profiles = [
            {"id": 1, "name": "Any"},
            {"id": 3, "name": "HD-720p"},
            {"id": 4, "name": "HD-1080p"},
        ]
        picked = choose_profile(
            profiles,
            preferred_names=["Ultra-HD", "HD-1080p", "HD-720p"],
        )
        self.assertEqual(picked["name"], "HD-1080p")

    def test_choose_root_folder(self):
        roots = [{"path": "/media/tv"}, {"path": "/media/other"}]
        self.assertEqual(choose_root_folder(roots, "/media/tv"), "/media/tv")
        self.assertEqual(choose_root_folder(roots, "/missing"), "/media/tv")

    def test_find_existing_servarr(self):
        existing = [
            {
                "id": 10,
                "name": "qBittorrent",
                "hostname": "qbittorrent",
                "port": 8080,
                "baseUrl": "",
                "is4k": False,
            }
        ]
        by_host = find_existing_servarr(
            existing,
            name="OtherName",
            hostname="qbittorrent",
            port=8080,
            base_url="",
            is4k=False,
        )
        self.assertIsNotNone(by_host)
        self.assertEqual(by_host["id"], 10)

    def test_normalize_remote_path_mappings(self):
        mappings = [
            {
                "host": "sabnzbd",
                "remote_path": "/config/Downloads/complete/",
                "local_path": "/data/usenet/completed/",
            },
            {
                "host": "SABNZBD",
                "remotePath": "/config/Downloads/complete",
                "localPath": "/other/path",
            },
            {"host": "", "remote_path": "/bad", "local_path": "/bad"},
            "not-a-dict",
        ]
        normalized = normalize_remote_path_mappings(mappings)
        self.assertEqual(
            normalized,
            [
                {
                    "host": "sabnzbd",
                    "remotePath": "/config/Downloads/complete",
                    "localPath": "/data/usenet/completed",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
