import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.servarr.arr_service import ArrService  # noqa: E402


def _service() -> ArrService:
    return ArrService(
        http_request=lambda *_args, **_kwargs: (200, [], ""),
        log=lambda _msg: None,
        field_map=lambda _fields: {},
        field_list=lambda _values: [],
        coerce_list=lambda value: value if isinstance(value, list) else [],
        to_int=lambda value, default=None: int(value) if value is not None else default,
        normalize_remote_path_mappings=lambda mappings: list(mappings or []),
    )


class ArrServiceCategorySelectionTests(unittest.TestCase):
    def test_choose_category_prefers_explicit_app_value(self):
        service = _service()
        selected = service.choose_category(
            {"name": "CustomTv", "implementation": "customtv", "category": "my-tv"},
            {"categories": {"customtv": "from-client"}},
        )
        self.assertEqual(selected, "my-tv")

    def test_choose_category_uses_client_mapping_before_capability_default(self):
        service = _service()
        selected = service.choose_category(
            {
                "name": "Radarr",
                "implementation": "radarr",
                "capabilities": {"default_download_category": "movies"},
            },
            {"categories": {"radarr": "movies-alt"}},
        )
        self.assertEqual(selected, "movies-alt")

    def test_choose_category_falls_back_to_capability_default_then_downloads(self):
        service = _service()
        selected_capability = service.choose_category(
            {
                "name": "Sonarr",
                "implementation": "sonarr",
                "capabilities": {"default_download_category": "tv"},
            },
            {},
        )
        selected_default = service.choose_category(
            {"name": "Unknown", "implementation": "custom-arr", "capabilities": {}},
            {},
        )
        self.assertEqual(selected_capability, "tv")
        self.assertEqual(selected_default, "downloads")


if __name__ == "__main__":
    unittest.main()
