"""Verify license references are consistent across the project.

Catches leakage where one file says Apache while the canonical LICENSE says AGPL-3.0.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CANONICAL_LICENSE = "AGPL-3.0"
CANONICAL_LICENSE_FULL = "GNU Affero General Public License"
WRONG_LICENSES = ["Apache License", "Apache-2.0", "MIT License", "BSD License"]

# Files where third-party license names naturally appear (dependency metadata)
ALLOWLIST_PATHS = {
    "package-lock.json",
    "package.json",
    "yarn.lock",
    ".venv",
    "node_modules",
    "prowlarr/Definitions",  # third-party indexer definitions
}


def _should_skip(path: Path) -> bool:
    rel = str(path.relative_to(ROOT))
    return any(skip in rel for skip in ALLOWLIST_PATHS)


class TestLicenseFileConsistency(unittest.TestCase):
    """Core license files must agree on the license type."""

    def test_license_file_is_agpl(self):
        license_file = ROOT / "LICENSE"
        self.assertTrue(license_file.is_file(), "LICENSE file missing")
        text = license_file.read_text(encoding="utf-8")
        self.assertIn(CANONICAL_LICENSE_FULL, text,
                      f"LICENSE file must contain '{CANONICAL_LICENSE_FULL}'")

    def test_notice_file_matches_license(self):
        notice_file = ROOT / "NOTICE"
        if not notice_file.is_file():
            self.skipTest("No NOTICE file")
        text = notice_file.read_text(encoding="utf-8")
        for wrong in WRONG_LICENSES:
            self.assertNotIn(wrong, text,
                             f"NOTICE file references '{wrong}' but project is {CANONICAL_LICENSE}")

    def test_dockerfiles_use_correct_spdx(self):
        for dockerfile in ROOT.glob("deploy/compose/*.Dockerfile"):
            text = dockerfile.read_text(encoding="utf-8")
            if "image.licenses" in text:
                self.assertIn(CANONICAL_LICENSE, text,
                              f"{dockerfile.name}: OCI label must use {CANONICAL_LICENSE}")
                for wrong in WRONG_LICENSES:
                    self.assertNotIn(wrong, text,
                                     f"{dockerfile.name}: references '{wrong}'")


class TestNoWrongLicenseInSource(unittest.TestCase):
    """Source files must not claim a different license than the project."""

    def test_no_apache_in_python_source(self):
        violations = []
        for py_file in (ROOT / "src").rglob("*.py"):
            if _should_skip(py_file):
                continue
            try:
                text = py_file.read_text(encoding="utf-8")
            except Exception:
                continue
            for wrong in WRONG_LICENSES:
                if wrong in text:
                    rel = py_file.relative_to(ROOT)
                    violations.append(f"{rel}: contains '{wrong}'")
        self.assertFalse(violations,
                         f"Source files reference wrong license:\n" +
                         "\n".join(f"  - {v}" for v in violations))

    def test_no_apache_in_yaml_manifests(self):
        violations = []
        for yaml_dir in [ROOT / "deploy" / "k8s", ROOT / "contracts"]:
            if not yaml_dir.is_dir():
                continue
            for yaml_file in yaml_dir.rglob("*.yaml"):
                if _should_skip(yaml_file):
                    continue
                try:
                    text = yaml_file.read_text(encoding="utf-8")
                except Exception:
                    continue
                for wrong in WRONG_LICENSES:
                    if wrong in text:
                        rel = yaml_file.relative_to(ROOT)
                        violations.append(f"{rel}: contains '{wrong}'")
        self.assertFalse(violations,
                         f"YAML files reference wrong license:\n" +
                         "\n".join(f"  - {v}" for v in violations))


if __name__ == "__main__":
    unittest.main()
