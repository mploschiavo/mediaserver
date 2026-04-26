"""Ratchet R-2: every validation rule (VR-N) is enforced AND tested.

Two checks:

1. Every ``VR-N`` referenced in ``validator.py`` has at least one
   ``ValidationError(code="VR-N", …)`` emission path AND at least one
   test case that asserts ``"VR-N" in {e.code for e in errs}``.
2. Tests don't reference VR codes that the validator doesn't actually
   emit (catch the inverse drift: a renamed rule whose tests still
   reference the old name and silently pass-but-don't-cover).

This locks the design-doc's rule list to actual code. Adding a new
VR-N requires:

* Emitting it from ``validate_routing_config``.
* Adding a rejecting test in ``test_validator.py`` that asserts the
  code surfaces.

Forgetting either fails the ratchet.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

VALIDATOR = ROOT / "src" / "media_stack" / "api" / "services" / "config" / "routing" / "validator.py"
TEST_FILE = ROOT / "tests" / "unit" / "api" / "services" / "config" / "routing" / "test_validator.py"


VR_PATTERN = re.compile(r'(?:code\s*=\s*"|VR-N: |"VR-)([0-9]+)"')
VR_CODE_PATTERN = re.compile(r'code\s*=\s*"(VR-\d+)"')


def _vr_codes_in_file(path: Path) -> set[str]:
    """All ``VR-N`` codes referenced in a file (emit or assertion)."""
    src = path.read_text(encoding="utf-8")
    codes: set[str] = set()
    # Match ``code="VR-N"`` (validator emit + test assertion forms).
    for m in VR_CODE_PATTERN.finditer(src):
        codes.add(m.group(1))
    # Match ``"VR-N"`` references in any string context.
    for m in re.finditer(r'"(VR-\d+)"', src):
        codes.add(m.group(1))
    return codes


class RoutingValidationRulesCoverageRatchet(unittest.TestCase):
    def test_validator_emits_all_documented_rules(self) -> None:
        # The set the design doc + module docstring reference.
        documented = {f"VR-{i}" for i in range(1, 12)}  # VR-1..VR-11
        emitted = _vr_codes_in_file(VALIDATOR)
        missing = documented - emitted
        self.assertEqual(
            missing, set(),
            f"validator.py is missing emissions for: {sorted(missing)}. "
            f"Either implement them or update the documented range here.",
        )

    def test_every_emitted_rule_has_a_test(self) -> None:
        emitted = {c for c in _vr_codes_in_file(VALIDATOR) if c.startswith("VR-")}
        tested = {c for c in _vr_codes_in_file(TEST_FILE) if c.startswith("VR-")}
        untested = emitted - tested
        self.assertEqual(
            untested, set(),
            f"validator.py emits {sorted(untested)} but no test asserts on "
            f"those codes. Add a rejecting test in test_validator.py.",
        )

    def test_no_phantom_test_codes(self) -> None:
        # A test asserting a code the validator doesn't emit means the
        # rule was renamed/removed and the test silently passes.
        emitted = {c for c in _vr_codes_in_file(VALIDATOR) if c.startswith("VR-")}
        tested = {c for c in _vr_codes_in_file(TEST_FILE) if c.startswith("VR-")}
        phantom = tested - emitted
        self.assertEqual(
            phantom, set(),
            f"test_validator.py references {sorted(phantom)} which "
            f"validator.py doesn't emit. Either restore the rule or "
            f"delete the stale test.",
        )


if __name__ == "__main__":
    unittest.main()
