import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.config_artifacts_service import ConfigArtifactsService  # noqa: E402


def _service():
    return ConfigArtifactsService(
        bool_cfg=lambda cfg, key, default=False: bool((cfg or {}).get(key, default)),
        coerce_list=lambda value: (
            value if isinstance(value, list) else ([] if value is None else [value])
        ),
        resolve_path=lambda base, rel: Path(base) / rel,
        normalize_url=lambda value: str(value).rstrip("/"),
        wait_for_service=lambda *args, **kwargs: None,
        resolve_jellyfin_api_key=lambda cfg, root: "abc123",
        jellyfin_request=lambda *args, **kwargs: (200, [], ""),
        log=lambda _msg: None,
        load_bootstrap_default_json=lambda _name, fallback: fallback,
        default_homepage_hosts=["jellyfin.local"],
        render_homepage_services_yaml=lambda hosts, scheme, onboarding: (
            f"hosts={hosts};scheme={scheme};onboarding={onboarding}"
        ),
    )


class ConfigArtifactsServiceTests(unittest.TestCase):
    def test_deep_merge_objects_nested(self):
        svc = _service()
        merged = svc.deep_merge_objects(
            {"a": {"b": 1, "c": 2}, "x": 10},
            {"a": {"c": 99}, "y": 20},
        )
        self.assertEqual(merged["a"]["b"], 1)
        self.assertEqual(merged["a"]["c"], 99)
        self.assertEqual(merged["x"], 10)
        self.assertEqual(merged["y"], 20)

    def test_render_yaml_scalar_and_list(self):
        svc = _service()
        lines = svc.render_yaml({"a": 1, "b": ["x", "y"], "c": True})
        rendered = "\n".join(lines)
        self.assertIn("a: 1", rendered)
        self.assertIn("- 'x'", rendered)
        self.assertIn("c: true", rendered)

    def test_ensure_homepage_services_config_writes_file(self):
        svc = _service()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {"homepage": {"enabled": True, "hosts": ["jellyfin.local"]}}
            changed = svc.ensure_homepage_services_config(cfg, tmp)
            self.assertTrue(changed)
            path = Path(tmp) / "homepage" / "services.yaml"
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
