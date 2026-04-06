import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.jellyfin.playback_service import (  # noqa: E402
    JellyfinPlaybackDependencies,
    JellyfinPlaybackService,
)


def _build_query_path(path: str, params: dict) -> str:
    if not params:
        return path
    pairs = [f"{k}={v}" for k, v in params.items()]
    sep = "&" if "?" in path else "?"
    return f"{path}{sep}{'&'.join(pairs)}"


class JellyfinPlaybackServiceTests(unittest.TestCase):
    def test_home_media_excludes_collections_and_playlists(self):
        posted_user_cfg = {}
        user_payload = {
            "Configuration": {
                "DisplayCollectionsView": True,
                "MyMediaExcludes": [],
            }
        }
        server_payload = {"PreferredMetadataLanguage": "en"}
        views_payload = {
            "Items": [
                {"Id": "collections-view", "CollectionType": "boxsets"},
                {"Id": "playlists-view", "CollectionType": "playlists"},
                {"Id": "movies-view", "CollectionType": "movies"},
            ]
        }

        def jellyfin_request(_url, path, _key, method="GET", payload=None):
            if method == "GET" and path == "/Users/u1":
                return 200, user_payload, ""
            if method == "GET" and path == "/Users/u1/Views":
                return 200, views_payload, ""
            if method == "GET" and path == "/System/Configuration":
                return 200, server_payload, ""
            if method == "POST" and path.startswith("/Users/Configuration"):
                posted_user_cfg.update(payload or {})
                return 204, {}, ""
            return 200, {}, ""

        deps = JellyfinPlaybackDependencies(
            log=lambda _msg: None,
            bool_cfg=lambda cfg, key, fallback: bool(cfg.get(key, fallback)),
            coerce_list=lambda value: (
                value if isinstance(value, list) else ([] if value is None else [value])
            ),
            normalize_url=lambda value: str(value).rstrip("/"),
            wait_for_service=lambda *_args, **_kwargs: None,
            resolve_api_key=lambda _cfg, _root: "api-key",
            jellyfin_request=jellyfin_request,
            build_query_path=_build_query_path,
            resolve_user_id=lambda _cfg, _url, _key: "u1",
            normalize_plugin_name=lambda value: str(value).strip().lower(),
        )
        service = JellyfinPlaybackService(deps=deps)

        cfg = {
            "jellyfin_playback": {
                "enabled": True,
                "url": "http://jellyfin:8096",
                "user_defaults": {
                    "DisplayCollectionsView": False,
                },
                "server_defaults": {
                    "PreferredMetadataLanguage": "en",
                },
                "display_preferences": {
                    "enabled": False,
                },
                "check_intro_skip_plugin": False,
                "home_media": {
                    "enabled": True,
                    "exclude_collections": True,
                    "exclude_playlists": True,
                    "cleanup_managed_exclusions": True,
                },
            }
        }

        service.ensure(cfg=cfg, config_root="/tmp", wait_timeout=5)

        self.assertEqual(posted_user_cfg.get("DisplayCollectionsView"), False)
        self.assertEqual(
            posted_user_cfg.get("MyMediaExcludes"),
            ["collections-view", "playlists-view"],
        )


if __name__ == "__main__":
    unittest.main()
