import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))


class ComposeEdgeProviderIsolationContractTests(unittest.TestCase):
    def test_shared_compose_services_has_no_provider_named_modules(self):
        services_dir = (
            ROOT / "src" / "media_stack" / "core" / "platforms" / "compose" / "services"
        )
        py_files = [path.name for path in services_dir.glob("*.py")]
        provider_named = [
            name for name in py_files if ("envoy" in name.lower() or "traefik" in name.lower())
        ]
        self.assertEqual(
            provider_named,
            [],
            "Provider-specific compose edge modules must live under "
            "src/media_stack/core/platforms/compose/edge/providers/<provider>/",
        )

    def test_provider_folders_contain_provider_runtime_implementation(self):
        providers_root = (
            ROOT / "src" / "media_stack" / "core" / "platforms" / "compose" / "edge" / "providers"
        )
        self.assertTrue((providers_root / "traefik" / "plugin.py").exists())
        self.assertTrue((providers_root / "traefik" / "patch_service.py").exists())
        self.assertTrue((providers_root / "envoy" / "plugin.py").exists())
        self.assertTrue((providers_root / "envoy" / "dynamic_config.py").exists())
        self.assertTrue((providers_root / "envoy" / "patch_service.py").exists())

    def test_shared_compose_modules_do_not_import_provider_specific_modules(self):
        compose_root = ROOT / "src" / "media_stack" / "core" / "platforms" / "compose"
        for path in compose_root.rglob("*.py"):
            rel = path.relative_to(compose_root).as_posix()
            if rel.startswith("edge/providers/"):
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("edge.providers.envoy", text, rel)
            self.assertNotIn("edge.providers.traefik", text, rel)

    def test_shared_adapter_has_no_provider_branching(self):
        adapter_path = (
            ROOT
            / "src"
            / "media_stack"
            / "core"
            / "platforms"
            / "compose"
            / "rebuild_platform_adapter.py"
        )
        text = adapter_path.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r'if\s+provider\s*==\s*"traefik"', text))
        self.assertIsNone(re.search(r'if\s+provider\s*==\s*"envoy"', text))


if __name__ == "__main__":
    unittest.main()
