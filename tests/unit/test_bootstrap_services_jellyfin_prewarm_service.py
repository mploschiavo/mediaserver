import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.jellyfin_prewarm_service import (  # noqa: E402
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
            self.assertTrue(any("book sidecar artwork reconcile complete" in line for line in logs))

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
            self.assertTrue(any("using fallback books root" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
