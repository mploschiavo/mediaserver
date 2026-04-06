import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.adapters.jellyfin import apply_artwork_profile, reorder_provider_names  # noqa: E402


class BootstrapJellyfinHelperTests(unittest.TestCase):
    def test_reorder_provider_names_applies_priority_fuzzy(self):
        ordered = reorder_provider_names(
            [
                "The Open Movie Database",
                "Screen Grabber",
                "Fanart",
                "TheMovieDb",
            ],
            ["TheMovieDb", "Fanart"],
        )
        self.assertEqual(ordered[0], "TheMovieDb")
        self.assertEqual(ordered[1], "Fanart")
        self.assertIn("The Open Movie Database", ordered)
        self.assertIn("Screen Grabber", ordered)

    def test_apply_artwork_profile_adds_and_updates_limits(self):
        result = apply_artwork_profile(
            image_options=[
                {"Type": "Backdrop", "Limit": 1, "MinWidth": 640},
                {"Type": "Primary", "Limit": 1, "MinWidth": 0},
            ],
            supported_image_types=["Primary", "Backdrop", "Logo"],
            profile={
                "Backdrop": {"limit": 3, "min_width": 1280},
                "Logo": {"limit": 1, "min_width": 0},
            },
        )
        by_type = {item["Type"]: item for item in result}
        self.assertEqual(by_type["Backdrop"]["Limit"], 3)
        self.assertEqual(by_type["Backdrop"]["MinWidth"], 1280)
        self.assertEqual(by_type["Logo"]["Limit"], 1)
        self.assertEqual(by_type["Logo"]["MinWidth"], 0)
        self.assertEqual(by_type["Primary"]["Limit"], 1)

    def test_apply_artwork_profile_respects_supported_types(self):
        result = apply_artwork_profile(
            image_options=[],
            supported_image_types=["Primary"],
            profile={"Logo": {"limit": 1, "min_width": 0}},
        )
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
