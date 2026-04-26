import re
import unittest
from pathlib import Path


class OptionalManifestPrewarmMountTests(unittest.TestCase):
    def test_jellyfin_prewarm_cronjob_mounts_media_pvc(self):
        root = Path(__file__).resolve().parents[3]
        manifest_path = root / "k8s" / "optional.yaml"
        text = manifest_path.read_text(encoding="utf-8")

        match = re.search(
            r"kind:\s*CronJob\s*[\s\S]*?name:\s*media-stack-jellyfin-prewarm[\s\S]*?(?=\n---\n|\Z)",
            text,
        )
        self.assertIsNotNone(match, "media-stack-jellyfin-prewarm CronJob block not found")
        block = match.group(0)

        self.assertIn("- name: stack-media", block)
        self.assertIn("mountPath: /srv-stack/media", block)
        self.assertIn("claimName: media-stack-media", block)


if __name__ == "__main__":
    unittest.main()
