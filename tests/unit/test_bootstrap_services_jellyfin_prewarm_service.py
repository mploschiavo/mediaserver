import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.jellyfin.prewarm_service import (  # noqa: E402
    JellyfinPrewarmDependencies,
    JellyfinPrewarmService,
)


class JellyfinPrewarmServiceTests(unittest.TestCase):
    def test_extracts_book_sidecar_artwork_from_epub_cover(self):
        logs = []
        deps = JellyfinPrewarmDependencies(
            log=logs.append,
            bool_cfg=lambda cfg, key, fallback: bool(cfg.get(key, fallback)),
            normalize_url=lambda value: str(value).rstrip("/"),
            wait_for_service=lambda *_args, **_kwargs: None,
            resolve_api_key=lambda _cfg, _root: "api-key",
            jellyfin_request=lambda *_args, **_kwargs: (200, {}, ""),
            build_query_path=lambda path, _params: path,
            trigger_livetv_refresh=lambda *_args, **_kwargs: (True, "ok"),
        )
        service = JellyfinPrewarmService(deps=deps)

        with tempfile.TemporaryDirectory() as tmp:
            books_root = Path(tmp) / "books" / "Author Name"
            books_root.mkdir(parents=True, exist_ok=True)
            epub_path = books_root / "Example Book.epub"
            cover_bytes = b"\x89PNG\r\n\x1a\nFAKEPNGDATA"
            with ZipFile(epub_path, "w") as archive:
                archive.writestr("cover.png", cover_bytes)
                archive.writestr("content.xhtml", "<html/>")

            cfg = {
                "jellyfin_prewarm": {
                    "enabled": True,
                    "url": "http://jellyfin:8096",
                    "refresh_library": False,
                    "refresh_channels": False,
                    "refresh_guide": False,
                    "book_sidecar_artwork": {
                        "enabled": True,
                        "books_root_path": str(Path(tmp) / "books"),
                        "output_filename": "folder.jpg",
                        "replace_existing": False,
                    },
                }
            }

            service.ensure(cfg=cfg, config_root="/tmp", wait_timeout=5)

            sidecar = books_root / "folder.jpg"
            self.assertTrue(sidecar.exists())
            self.assertEqual(sidecar.read_bytes(), cover_bytes)
            self.assertTrue((books_root / "Example Book.jpg").exists())
            self.assertTrue(any("book sidecar artwork reconcile complete" in line for line in logs))

    def test_extracts_music_sidecar_artwork_from_cover_file(self):
        logs = []
        deps = JellyfinPrewarmDependencies(
            log=logs.append,
            bool_cfg=lambda cfg, key, fallback: bool(cfg.get(key, fallback)),
            normalize_url=lambda value: str(value).rstrip("/"),
            wait_for_service=lambda *_args, **_kwargs: None,
            resolve_api_key=lambda _cfg, _root: "api-key",
            jellyfin_request=lambda *_args, **_kwargs: (200, [], ""),
            build_query_path=lambda path, _params: path,
            trigger_livetv_refresh=lambda *_args, **_kwargs: (True, "ok"),
        )
        service = JellyfinPrewarmService(deps=deps)

        with tempfile.TemporaryDirectory() as tmp:
            album_dir = Path(tmp) / "music" / "Artist Name" / "Album Name"
            album_dir.mkdir(parents=True, exist_ok=True)
            (album_dir / "track01.mp3").write_bytes(b"FAKEAUDIO")
            cover_bytes = b"\x89PNG\r\n\x1a\nMUSICCOVER"
            (album_dir / "cover.png").write_bytes(cover_bytes)

            cfg = {
                "jellyfin_prewarm": {
                    "enabled": True,
                    "url": "http://jellyfin:8096",
                    "refresh_library": False,
                    "refresh_channels": False,
                    "refresh_guide": False,
                    "artwork_health_check": {"enabled": False},
                    "book_sidecar_artwork": {"enabled": False},
                    "music_sidecar_artwork": {
                        "enabled": True,
                        "music_root_path": str(Path(tmp) / "music"),
                        "output_filename": "folder.jpg",
                        "replace_existing": False,
                    },
                }
            }

            service.ensure(cfg=cfg, config_root="/tmp", wait_timeout=5)

            sidecar = album_dir / "folder.jpg"
            self.assertTrue(sidecar.exists())
            self.assertEqual(sidecar.read_bytes(), cover_bytes)
            self.assertTrue(
                any("music sidecar artwork reconcile complete" in line for line in logs)
            )

    def test_uses_fallback_books_root_path_when_primary_missing(self):
        logs = []
        deps = JellyfinPrewarmDependencies(
            log=logs.append,
            bool_cfg=lambda cfg, key, fallback: bool(cfg.get(key, fallback)),
            normalize_url=lambda value: str(value).rstrip("/"),
            wait_for_service=lambda *_args, **_kwargs: None,
            resolve_api_key=lambda _cfg, _root: "api-key",
            jellyfin_request=lambda *_args, **_kwargs: (200, {}, ""),
            build_query_path=lambda path, _params: path,
            trigger_livetv_refresh=lambda *_args, **_kwargs: (True, "ok"),
        )
        service = JellyfinPrewarmService(deps=deps)

        with tempfile.TemporaryDirectory() as tmp:
            fallback_books_root = Path(tmp) / "fallback" / "books" / "Author Name"
            fallback_books_root.mkdir(parents=True, exist_ok=True)
            epub_path = fallback_books_root / "Fallback Book.epub"
            cover_bytes = b"\x89PNG\r\n\x1a\nFALLBACKDATA"
            with ZipFile(epub_path, "w") as archive:
                archive.writestr("cover.png", cover_bytes)
                archive.writestr("content.xhtml", "<html/>")

            cfg = {
                "jellyfin_prewarm": {
                    "enabled": True,
                    "url": "http://jellyfin:8096",
                    "refresh_library": False,
                    "refresh_channels": False,
                    "refresh_guide": False,
                    "book_sidecar_artwork": {
                        "enabled": True,
                        "books_root_path": str(Path(tmp) / "missing" / "books"),
                        "books_root_paths": [
                            str(Path(tmp) / "missing" / "books"),
                            str(Path(tmp) / "fallback" / "books"),
                        ],
                        "output_filename": "folder.jpg",
                        "replace_existing": False,
                    },
                }
            }

            service.ensure(cfg=cfg, config_root="/tmp", wait_timeout=5)

            sidecar = fallback_books_root / "folder.jpg"
            self.assertTrue(sidecar.exists())
            self.assertEqual(sidecar.read_bytes(), cover_bytes)
            self.assertTrue((fallback_books_root / "Fallback Book.jpg").exists())
            self.assertTrue(any("using fallback books root" in line for line in logs))

    def test_writes_per_book_sidecar_even_when_folder_art_exists(self):
        logs = []
        deps = JellyfinPrewarmDependencies(
            log=logs.append,
            bool_cfg=lambda cfg, key, fallback: bool(cfg.get(key, fallback)),
            normalize_url=lambda value: str(value).rstrip("/"),
            wait_for_service=lambda *_args, **_kwargs: None,
            resolve_api_key=lambda _cfg, _root: "api-key",
            jellyfin_request=lambda *_args, **_kwargs: (200, {}, ""),
            build_query_path=lambda path, _params: path,
            trigger_livetv_refresh=lambda *_args, **_kwargs: (True, "ok"),
        )
        service = JellyfinPrewarmService(deps=deps)

        with tempfile.TemporaryDirectory() as tmp:
            books_root = Path(tmp) / "books" / "Author Name"
            books_root.mkdir(parents=True, exist_ok=True)
            epub_path = books_root / "Another Book.epub"
            cover_bytes = b"\x89PNG\r\n\x1a\nANOTHERBOOK"
            with ZipFile(epub_path, "w") as archive:
                archive.writestr("cover.png", cover_bytes)
                archive.writestr("content.xhtml", "<html/>")

            # Existing folder artwork should not block per-book sidecar creation.
            (books_root / "folder.jpg").write_bytes(b"EXISTING")

            cfg = {
                "jellyfin_prewarm": {
                    "enabled": True,
                    "url": "http://jellyfin:8096",
                    "refresh_library": False,
                    "refresh_channels": False,
                    "refresh_guide": False,
                    "artwork_health_check": {"enabled": False},
                    "book_sidecar_artwork": {
                        "enabled": True,
                        "books_root_path": str(Path(tmp) / "books"),
                        "output_filename": "folder.jpg",
                        "replace_existing": False,
                        "write_per_book_sidecars": True,
                    },
                }
            }

            service.ensure(cfg=cfg, config_root="/tmp", wait_timeout=5)
            per_book = books_root / "Another Book.jpg"
            self.assertTrue(per_book.exists())
            self.assertEqual(per_book.read_bytes(), cover_bytes)

    def test_artwork_health_check_covers_all_media_surfaces(self):
        logs = []

        def jellyfin_request(_base, path, _key, method="GET", payload=None, timeout=30):
            del method, payload, timeout
            if path.startswith("/Library/VirtualFolders"):
                return (
                    200,
                    [
                        {"Name": "Movies", "ItemId": "lib-movies", "CollectionType": "movies"},
                        {"Name": "TV Shows", "ItemId": "lib-tv", "CollectionType": "tvshows"},
                        {"Name": "Music", "ItemId": "lib-music", "CollectionType": "music"},
                        {"Name": "Books", "ItemId": "lib-books", "CollectionType": "books"},
                    ],
                    "",
                )
            if path.startswith("/Items?ParentId=lib-movies"):
                return (200, {"Items": [{"ImageTags": {"Primary": "x"}}]}, "")
            if path.startswith("/Items?ParentId=lib-tv"):
                return (200, {"Items": [{"PrimaryImageTag": "x"}]}, "")
            if path.startswith("/Items?ParentId=lib-music"):
                return (200, {"Items": [{"AlbumPrimaryImageTag": "x"}]}, "")
            if path.startswith("/Items?ParentId=lib-books"):
                return (200, {"Items": [{"ImageTags": {"Primary": "x"}}]}, "")
            if path.startswith("/LiveTv/Programs"):
                return (200, {"Items": [{"BackdropImageTags": ["bg"]}]}, "")
            return (200, {}, "")

        deps = JellyfinPrewarmDependencies(
            log=logs.append,
            bool_cfg=lambda cfg, key, fallback: bool(cfg.get(key, fallback)),
            normalize_url=lambda value: str(value).rstrip("/"),
            wait_for_service=lambda *_args, **_kwargs: None,
            resolve_api_key=lambda _cfg, _root: "api-key",
            jellyfin_request=jellyfin_request,
            build_query_path=lambda path, params: path
            + (
                "?" + "&".join(f"{k}={v}" for k, v in (params or {}).items() if v not in (None, ""))
                if params
                else ""
            ),
            trigger_livetv_refresh=lambda *_args, **_kwargs: (True, "ok"),
        )
        service = JellyfinPrewarmService(deps=deps)

        cfg = {
            "jellyfin_prewarm": {
                "enabled": True,
                "url": "http://jellyfin:8096",
                "refresh_library": False,
                "refresh_channels": False,
                "refresh_guide": False,
                "book_sidecar_artwork": {"enabled": False},
                "music_sidecar_artwork": {"enabled": False},
                "artwork_health_check": {
                    "enabled": True,
                    "required": True,
                    "libraries": ["Movies", "TV Shows", "Music", "Books", "Live TV"],
                    "warn_below_coverage_percent": 10,
                    "fail_below_coverage_percent": 5,
                },
            }
        }

        service.ensure(cfg=cfg, config_root="/tmp", wait_timeout=5)

        expected_labels = (
            "artwork coverage for Movies",
            "artwork coverage for TV Shows",
            "artwork coverage for Music",
            "artwork coverage for Books",
            "artwork coverage for Live TV",
        )
        for label in expected_labels:
            self.assertTrue(any(label in line for line in logs), label)

    def test_metadata_backfill_refreshes_items_missing_artwork_or_overview(self):
        logs = []
        refresh_calls = []

        def jellyfin_request(_base, path, _key, method="GET", payload=None, timeout=30):
            del payload, timeout
            if path.startswith("/Library/VirtualFolders"):
                return (
                    200,
                    [
                        {"Name": "Movies", "ItemId": "lib-movies", "CollectionType": "movies"},
                    ],
                    "",
                )
            if path.startswith("/Items?ParentId=lib-movies"):
                return (
                    200,
                    {
                        "Items": [
                            {"Id": "movie-a", "ImageTags": {}, "Overview": ""},
                            {"Id": "movie-b", "ImageTags": {"Primary": "x"}, "Overview": ""},
                            {
                                "Id": "movie-c",
                                "ImageTags": {"Primary": "x"},
                                "Overview": "Has summary",
                            },
                        ]
                    },
                    "",
                )
            if (
                path.startswith("/Items/movie-a/Refresh")
                or path.startswith("/Items/movie-b/Refresh")
                or path.startswith("/Items/lib-movies/Refresh")
            ):
                refresh_calls.append((method, path))
                return (204, {}, "")
            return (200, {}, "")

        deps = JellyfinPrewarmDependencies(
            log=logs.append,
            bool_cfg=lambda cfg, key, fallback: bool(cfg.get(key, fallback)),
            normalize_url=lambda value: str(value).rstrip("/"),
            wait_for_service=lambda *_args, **_kwargs: None,
            resolve_api_key=lambda _cfg, _root: "api-key",
            jellyfin_request=jellyfin_request,
            build_query_path=lambda path, params: path
            + (
                "?" + "&".join(f"{k}={v}" for k, v in (params or {}).items() if v not in (None, ""))
                if params
                else ""
            ),
            trigger_livetv_refresh=lambda *_args, **_kwargs: (True, "ok"),
        )
        service = JellyfinPrewarmService(deps=deps)

        cfg = {
            "jellyfin_prewarm": {
                "enabled": True,
                "url": "http://jellyfin:8096",
                "refresh_library": False,
                "refresh_channels": False,
                "refresh_guide": False,
                "book_sidecar_artwork": {"enabled": False},
                "music_sidecar_artwork": {"enabled": False},
                "metadata_backfill": {
                    "enabled": True,
                    "libraries": ["Movies"],
                    "refresh_missing_primary_image": True,
                    "refresh_missing_overview": True,
                    "refresh_collection_folder_images": True,
                    "max_refresh_per_library": 10,
                },
                "artwork_health_check": {"enabled": False},
            }
        }

        service.ensure(cfg=cfg, config_root="/tmp", wait_timeout=5)
        refreshed_paths = [path for _method, path in refresh_calls]
        self.assertTrue(any(path.startswith("/Items/movie-a/Refresh") for path in refreshed_paths))
        self.assertTrue(any(path.startswith("/Items/movie-b/Refresh") for path in refreshed_paths))
        self.assertFalse(any(path.startswith("/Items/movie-c/Refresh") for path in refreshed_paths))
        self.assertTrue(
            any(path.startswith("/Items/lib-movies/Refresh") for path in refreshed_paths)
        )
        self.assertTrue(any("metadata backfill complete" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
