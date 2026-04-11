"""Guardrail tests: prevent configuration sprawl and data duplication.

These tests enforce that:
1. Service configuration lives ONLY in per-service YAML contracts
2. No generated/compiled config JSON files are checked into the repo
3. Bootstrap jobs read from service YAMLs, not from generated JSON
4. Handlers receive flat keys from contract YAML defaults, not nested JSON
"""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

CONTRACTS_DIR = ROOT / "contracts"
SERVICES_DIR = CONTRACTS_DIR / "services"


class TestNoCheckedInConfigJson(unittest.TestCase):
    """The generated config JSON must NOT be checked into the repo."""

    def test_no_media_stack_config_json_in_contracts(self):
        """contracts/media-stack.config.json must not exist.

        All configuration lives in per-service YAML contracts
        (contracts/services/*.yaml). A compiled JSON was removed
        to eliminate duplication. If this test fails, someone
        re-created the file — delete it and read from YAMLs instead.
        """
        bad_path = CONTRACTS_DIR / "media-stack.config.json"
        self.assertFalse(
            bad_path.exists(),
            f"STOP: {bad_path} exists but should not. "
            "All config lives in contracts/services/*.yaml. "
            "Delete this file — it duplicates the service YAMLs."
        )

    def test_no_generated_config_json_anywhere_in_repo(self):
        """No generated config JSON should be tracked in git."""
        for p in ROOT.rglob("*.config.json"):
            # Skip node_modules, .venv, etc.
            rel = p.relative_to(ROOT)
            parts = str(rel).split(os.sep)
            if any(skip in parts for skip in ("node_modules", ".venv", "__pycache__", ".git", ".state")):
                continue
            self.fail(
                f"Generated config JSON found at {rel}. "
                "Config must live in service YAMLs, not compiled JSON."
            )


class TestServiceYamlIsSourceOfTruth(unittest.TestCase):
    """Service YAML contracts must be the sole source of service defaults."""

    def test_service_yamls_exist(self):
        self.assertTrue(SERVICES_DIR.is_dir(), "contracts/services/ directory must exist")
        yamls = list(SERVICES_DIR.glob("*.yaml"))
        self.assertGreater(len(yamls), 0, "No service YAML files found")

    def test_jellyfin_yaml_has_defaults(self):
        import yaml
        jf = SERVICES_DIR / "jellyfin.yaml"
        self.assertTrue(jf.is_file())
        data = yaml.safe_load(jf.read_text())
        defaults = data.get("defaults", {})
        self.assertIn("libraries", defaults)
        self.assertIn("livetv", defaults)
        self.assertIn("plugins", defaults)
        self.assertIn("playback", defaults)

    def test_every_service_yaml_has_id(self):
        import yaml
        for f in SERVICES_DIR.glob("*.yaml"):
            if f.name.startswith("_"):
                continue
            data = yaml.safe_load(f.read_text()) or {}
            svc = data.get("service", {})
            if not svc:
                continue  # stub/placeholder files
            svc_id = svc.get("id", "")
            self.assertTrue(svc_id, f"{f.name} has 'service' section but no 'id'")


class TestBootstrapJobsReadFromContracts(unittest.TestCase):
    """Bootstrap jobs must read config from service YAMLs, not JSON files."""

    def test_job_context_cfg_has_flat_jellyfin_keys(self):
        """JobContext.cfg must produce flat keys from service YAML defaults."""
        from media_stack.cli.commands.job_framework import JobContext
        ctx = JobContext()
        cfg = ctx.cfg
        for key in ["jellyfin_libraries", "jellyfin_livetv", "jellyfin_plugins", "jellyfin_playback"]:
            self.assertIn(key, cfg, f"JobContext.cfg missing {key}")

    def test_job_context_cfg_does_not_have_nested_jellyfin(self):
        """Config must NOT have a nested 'jellyfin' key with sub-sections."""
        from media_stack.cli.commands.job_framework import JobContext
        ctx = JobContext()
        cfg = ctx.cfg
        if "jellyfin" in cfg:
            self.assertNotIsInstance(
                cfg["jellyfin"].get("libraries") if isinstance(cfg["jellyfin"], dict) else None,
                dict,
                "cfg['jellyfin']['libraries'] found — must be flat: cfg['jellyfin_libraries']"
            )

    def test_no_config_json_import_in_job_framework(self):
        """job_framework.py must NOT load from JSON config files."""
        import inspect
        from media_stack.cli.commands import job_framework
        source = inspect.getsource(job_framework)
        self.assertNotIn("config.json", source, "job_framework.py must not reference config.json")
        self.assertNotIn("_resolve_config_path", source, "job_framework.py must not use _resolve_config_path")


class TestNoConfigDuplication(unittest.TestCase):
    """Configuration data must not be duplicated across files."""

    def test_handler_keys_match_yaml_defaults(self):
        """Every cfg.get() key in jellyfin handlers must correspond to a YAML defaults sub-section."""
        import re
        import yaml

        # Load jellyfin YAML defaults sub-section names
        jf_data = yaml.safe_load((SERVICES_DIR / "jellyfin.yaml").read_text())
        yaml_sub_keys = set(jf_data.get("defaults", {}).keys())

        # Find all cfg.get("jellyfin_*") patterns in handler code
        handler_dir = ROOT / "src" / "media_stack" / "services" / "apps" / "jellyfin"
        handler_keys = set()
        for py_file in handler_dir.glob("*_service.py"):
            content = py_file.read_text()
            for match in re.findall(r'cfg\.get\("jellyfin_(\w+)"\)', content):
                handler_keys.add(match)

        # Every handler key must have a corresponding YAML sub-section
        for key in handler_keys:
            self.assertIn(
                key, yaml_sub_keys,
                f"Handler reads cfg.get('jellyfin_{key}') but '{key}' "
                f"not found in jellyfin.yaml defaults. Add it to the YAML."
            )


class TestProfileIntegrity(unittest.TestCase):
    """Profile YAML must be valid and not have duplicate keys."""

    PROFILE = CONTRACTS_DIR / "media-stack.profile.yaml"

    def test_no_duplicate_top_level_keys(self):
        """YAML duplicate keys silently take last value — detect them."""
        import re
        content = self.PROFILE.read_text()
        top_keys = re.findall(r'^(\w[\w_]*):', content, re.MULTILINE)
        seen = {}
        for key in top_keys:
            if key in seen:
                self.fail(
                    f"Duplicate top-level key '{key}' in profile YAML "
                    f"(first at line ~{seen[key]}, again later). "
                    "YAML silently uses the last value — earlier data is LOST."
                )
            seen[key] = top_keys.index(key) + 1

    def test_metadata_has_name(self):
        import yaml
        data = yaml.safe_load(self.PROFILE.read_text())
        meta = data.get("metadata", {})
        self.assertIn("name", meta, "metadata.name missing — profile validation will fail")
        self.assertIn("platform", meta, "metadata.platform missing")

    def test_profile_passes_validation(self):
        from media_stack.api.preflight.profile_validation import validate_profile
        result = validate_profile(str(self.PROFILE))
        self.assertIsInstance(result, dict)
        self.assertIn("metadata", result)


if __name__ == "__main__":
    unittest.main()
