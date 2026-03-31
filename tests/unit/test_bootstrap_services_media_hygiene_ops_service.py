import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.media_hygiene_ops_service import MediaHygieneOpsService  # noqa: E402


class MediaHygieneOpsServiceTests(unittest.TestCase):
    def _svc(self) -> MediaHygieneOpsService:
        return MediaHygieneOpsService(
            log=lambda _msg: None,
            bool_cfg=lambda cfg, key, default: bool(cfg.get(key, default)),
            coerce_list=lambda value: value if isinstance(value, list) else [],
            to_int=lambda value, default=None: int(value) if value is not None else default,
            to_float=lambda value, default=None: float(value) if value is not None else default,
            normalize_token=lambda value: str(value).strip().lower(),
            normalize_url=lambda value: str(value).rstrip("/"),
            qbit_login=lambda *_args, **_kwargs: None,
            qbit_list_completed_torrents=lambda *_args, **_kwargs: [],
            qbit_list_torrents=lambda *_args, **_kwargs: [],
            qbit_delete_torrents=lambda *_args, **_kwargs: None,
            qbit_set_preferences=lambda *_args, **_kwargs: None,
        )

    def test_run_filesystem_hygiene_preserves_configured_empty_dirs(self):
        svc = self._svc()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "root"
            keep_dir = root / "completed" / "tv"
            drop_dir = root / "completed" / "orphan"
            keep_dir.mkdir(parents=True, exist_ok=True)
            drop_dir.mkdir(parents=True, exist_ok=True)

            summary = svc.run_filesystem_hygiene(
                {
                    "filesystem": {
                        "enabled": True,
                        "roots": [str(root)],
                        "remove_empty_dirs": True,
                        "preserve_empty_dirs": [str(keep_dir)],
                    }
                }
            )

            self.assertTrue(keep_dir.exists())
            self.assertFalse(drop_dir.exists())
            self.assertEqual(summary.get("removed_empty_dirs"), 1)


if __name__ == "__main__":
    unittest.main()
