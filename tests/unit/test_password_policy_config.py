"""Unit tests for PasswordPolicyConfig (load/save/bounds/clamp)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.password_policy_config import (  # noqa: E402
    PasswordPolicyConfig,
)


class LoadValuesTests(unittest.TestCase):
    def test_defaults_when_no_file(self):
        """Fresh install with no file must return sane defaults,
        not zero/None. A missing file must never silently disable
        enforcement."""
        with tempfile.TemporaryDirectory() as d:
            cfg = PasswordPolicyConfig(Path(d))
            v = cfg.load_values()
        self.assertEqual(v["min_length"], 12)
        self.assertEqual(v["require_classes"], 3)
        self.assertEqual(v["history_len"], 5)

    def test_roundtrip_through_save(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = PasswordPolicyConfig(Path(d))
            stored = cfg.save_values({
                "min_length": 16, "require_classes": 4, "history_len": 10,
            })
            self.assertEqual(stored["min_length"], 16)
            self.assertEqual(stored["require_classes"], 4)
            self.assertEqual(stored["history_len"], 10)
            # Re-read
            v = cfg.load_values()
            self.assertEqual(v, stored)

    def test_clamps_out_of_range_on_save(self):
        """Admin typo (min_length: 3) must NOT weaken the policy
        below the floor. The stored value is clamped to the floor."""
        with tempfile.TemporaryDirectory() as d:
            cfg = PasswordPolicyConfig(Path(d))
            stored = cfg.save_values({
                "min_length": 2,       # below floor
                "require_classes": 99,  # above ceiling
                "history_len": -1,      # below floor
            })
            bounds = cfg.bounds()
            self.assertEqual(stored["min_length"],
                             bounds["min_length"]["floor"])
            self.assertEqual(stored["require_classes"],
                             bounds["require_classes"]["ceiling"])
            self.assertEqual(stored["history_len"],
                             bounds["history_len"]["floor"])

    def test_nonint_values_fall_back_to_current(self):
        """Malformed input (e.g. 'abc') must not wipe the stored value."""
        with tempfile.TemporaryDirectory() as d:
            cfg = PasswordPolicyConfig(Path(d))
            cfg.save_values({"min_length": 14})
            stored = cfg.save_values({"min_length": "banana"})
            self.assertEqual(stored["min_length"], 14)

    def test_partial_update_preserves_other_fields(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = PasswordPolicyConfig(Path(d))
            cfg.save_values({
                "min_length": 14, "require_classes": 2, "history_len": 7,
            })
            stored = cfg.save_values({"require_classes": 4})
            self.assertEqual(stored["min_length"], 14)
            self.assertEqual(stored["require_classes"], 4)
            self.assertEqual(stored["history_len"], 7)


class BuildPolicyTests(unittest.TestCase):
    def test_build_uses_current_values(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = PasswordPolicyConfig(Path(d))
            cfg.save_values({
                "min_length": 16, "require_classes": 4, "history_len": 10,
            })
            pol = cfg.build_policy()
        self.assertEqual(pol.min_length, 16)
        self.assertEqual(pol.required_classes, 4)
        self.assertEqual(pol.history_len, 10)

    def test_build_rejects_weak_password_after_policy_tightened(self):
        """The policy object produced after save_values() MUST enforce
        the new settings — regression guard for the case where the
        config was persisted but the factory cached a stale policy."""
        with tempfile.TemporaryDirectory() as d:
            cfg = PasswordPolicyConfig(Path(d))
            cfg.save_values({"min_length": 20})
            pol = cfg.build_policy()
            result = pol.check_candidate("Short1!")
        self.assertFalse(result.ok)
        self.assertIn("too short", result.reason)


class FactoryLiveReloadTests(unittest.TestCase):
    """Prove the UserServiceFactory doesn't cache the policy — a
    dashboard edit to /api/password-policy must take effect on the
    very next /api/users/X/reset-password call, with no controller
    restart needed."""

    def test_policy_change_takes_effect_on_next_build(self):
        """Simulate the admin flow: build (old policy) → edit file
        via save_values → build again → new policy enforced.
        Captures the 'stale cached policy' regression class."""
        with tempfile.TemporaryDirectory() as d:
            cfg = PasswordPolicyConfig(Path(d))
            cfg.save_values({"min_length": 8})
            weak_ok = cfg.build_policy().check_candidate("Weak1234")
            self.assertTrue(
                weak_ok.ok,
                "8-char password should pass an 8-char-min policy.",
            )
            # Admin tightens policy.
            cfg.save_values({"min_length": 20})
            still_weak = cfg.build_policy().check_candidate("Weak1234")
            self.assertFalse(
                still_weak.ok,
                "Tightened policy didn't apply on next build_policy() — "
                "a stale cache or class-default fallback is in the "
                "way, and a dashboard edit requires a restart to "
                "take effect.",
            )


class BoundsTests(unittest.TestCase):
    def test_bounds_expose_floor_ceiling_default(self):
        cfg = PasswordPolicyConfig(Path("/tmp"))
        b = cfg.bounds()
        for key in ("min_length", "require_classes", "history_len"):
            self.assertIn(key, b)
            self.assertIn("floor", b[key])
            self.assertIn("ceiling", b[key])
            self.assertIn("default", b[key])
            self.assertLess(b[key]["floor"], b[key]["ceiling"])


class PolicyPersistenceRoundTripTests(unittest.TestCase):
    """Regression guards: the policy file must survive across
    controller restarts AND configure-auth regens. The 2026-04-19 bug
    was that a stale weak policy (min_length: 4) sat on disk and was
    picked up by every new build_policy() call — admin couldn't tell
    why "easy1" was accepted instead of rejected."""

    def test_file_survives_controller_rebuild_cycle(self):
        """Save → instantiate new PasswordPolicyConfig (new process)
        → values match. This emulates a controller restart."""
        with tempfile.TemporaryDirectory() as d:
            cfg_a = PasswordPolicyConfig(Path(d))
            cfg_a.save_values({
                "min_length": 14, "require_classes": 3, "history_len": 5,
            })
            # "New process" = fresh instance with same config root.
            cfg_b = PasswordPolicyConfig(Path(d))
            v = cfg_b.load_values()
        # The v1.0.182 shape includes booleans + lockout fields too;
        # only assert the load-bearing values here so adding fields
        # in a future expansion doesn't break this regression guard.
        self.assertEqual(v["min_length"], 14)
        self.assertEqual(v["require_classes"], 3)
        self.assertEqual(v["history_len"], 5)

    def test_policy_builder_uses_file_not_class_defaults(self):
        """A regression where UserServiceFactory built PasswordPolicy()
        with NO args would silently use the 12/3/5 defaults even if
        the admin had explicitly set min_length to 20. The build_policy
        helper MUST read the file."""
        with tempfile.TemporaryDirectory() as d:
            cfg = PasswordPolicyConfig(Path(d))
            cfg.save_values({
                "min_length": 20, "require_classes": 4, "history_len": 10,
            })
            pol = cfg.build_policy()
            self.assertEqual(pol.min_length, 20)
            self.assertEqual(pol.required_classes, 4)
            # The exact flow that failed in production: admin sets 20,
            # user submits a 12-char password, policy should reject.
            self.assertFalse(pol.check_candidate("Secret123Abc").ok)

    def test_regen_from_defaults_when_file_missing(self):
        """If someone deletes the policy file mid-operation, the
        validator must fall back to SECURE defaults, never 'allow
        everything'. This was the silent failure mode where the
        ratchet floor (4 chars) became the effective policy."""
        with tempfile.TemporaryDirectory() as d:
            cfg = PasswordPolicyConfig(Path(d))
            # File absent. build_policy should still reject 'easy'.
            pol = cfg.build_policy()
            self.assertFalse(pol.check_candidate("easy").ok)
            self.assertFalse(pol.check_candidate("easy1").ok)
            self.assertTrue(pol.check_candidate(
                "LongSecureP@ss123").ok)

    def test_weak_policy_file_cannot_go_below_floor(self):
        """Even if someone hand-edits the file to
        ``min_length: 1``, the load clamps to the floor (4) —
        below 4 chars is trivially brute-forceable regardless of
        operator preference."""
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / ".controller" / "password-policy.yaml"
            target.parent.mkdir(parents=True)
            import yaml as _yaml
            target.write_text(_yaml.safe_dump(
                {"password_policy": {
                    "min_length": 1, "require_classes": 0, "history_len": -5,
                }}))
            cfg = PasswordPolicyConfig(Path(d))
            values = cfg.load_values()
            self.assertGreaterEqual(values["min_length"], 4)
            self.assertGreaterEqual(values["require_classes"], 1)
            self.assertGreaterEqual(values["history_len"], 0)


class V1_0_182ExpansionTests(unittest.TestCase):
    """v1.0.182 introduced explicit class-booleans + max_age_days +
    lockout fields. The legacy ``require_classes`` integer is kept
    on the read side (derived from the booleans) but ignored on the
    write side when explicit booleans are provided."""

    def test_load_exposes_class_booleans(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = PasswordPolicyConfig(Path(d))
            cfg.save_values({
                "min_length": 14,
                "require_uppercase": True,
                "require_lowercase": True,
                "require_digit": True,
                "require_special": False,
                "history_len": 5,
            })
            v = cfg.load_values()
        self.assertTrue(v["require_uppercase"])
        self.assertTrue(v["require_lowercase"])
        self.assertTrue(v["require_digit"])
        self.assertFalse(v["require_special"])
        # The legacy integer is derived from the booleans (3 of 4 on).
        self.assertEqual(v["require_classes"], 3)

    def test_load_exposes_lockout_and_rotation_fields(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = PasswordPolicyConfig(Path(d))
            v = cfg.load_values()
        self.assertEqual(v["max_age_days"], 0)
        self.assertEqual(v["lockout_threshold"], 5)
        self.assertEqual(v["lockout_window_minutes"], 15)

    def test_lockout_bounds_present(self):
        cfg = PasswordPolicyConfig(Path("/tmp"))
        b = cfg.bounds()
        self.assertIn("max_age_days", b)
        self.assertIn("lockout_threshold", b)
        self.assertIn("lockout_window_minutes", b)
        self.assertEqual(b["max_age_days"]["ceiling"], 365)
        self.assertEqual(b["lockout_window_minutes"]["ceiling"], 1440)

    def test_all_boolean_save_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = PasswordPolicyConfig(Path(d))
            cfg.save_values({
                "min_length": 16,
                "require_uppercase": True,
                "require_lowercase": True,
                "require_digit": True,
                "require_special": True,
                "history_len": 8,
                "max_age_days": 90,
                "lockout_threshold": 3,
                "lockout_window_minutes": 30,
            })
            cfg2 = PasswordPolicyConfig(Path(d))
            v = cfg2.load_values()
        self.assertEqual(v["min_length"], 16)
        self.assertTrue(v["require_special"])
        self.assertEqual(v["max_age_days"], 90)
        self.assertEqual(v["lockout_threshold"], 3)
        self.assertEqual(v["lockout_window_minutes"], 30)
        # Booleans → derived integer = 4
        self.assertEqual(v["require_classes"], 4)

    def test_legacy_blob_migrates_on_first_read(self):
        """A blob persisted by v1.0.181 (or earlier) has only the
        integer ``require_classes`` and lacks booleans + max_age_days
        + lockout fields. The first ``load_values()`` derives the
        booleans + writes the migrated blob back."""
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / ".controller" / "password-policy.yaml"
            target.parent.mkdir(parents=True)
            target.write_text(yaml.safe_dump(
                {"password_policy": {
                    "min_length": 12,
                    "require_classes": 3,
                    "history_len": 5,
                }}))
            cfg = PasswordPolicyConfig(Path(d))
            v = cfg.load_values()
            # require_classes=3 ⇒ upper+lower+digit, no special
            self.assertTrue(v["require_uppercase"])
            self.assertTrue(v["require_lowercase"])
            self.assertTrue(v["require_digit"])
            self.assertFalse(v["require_special"])
            # New fields seeded with defaults
            self.assertEqual(v["max_age_days"], 0)
            self.assertEqual(v["lockout_threshold"], 5)
            # And the file was rewritten with the booleans
            written = yaml.safe_load(target.read_text())["password_policy"]
            self.assertIn("require_uppercase", written)
            self.assertIn("max_age_days", written)

    def test_legacy_require_classes_4_promotes_special(self):
        """When the legacy blob had ``require_classes: 4`` the
        migration promotes ``require_special`` to True so the post-
        migration policy is at least as strict."""
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / ".controller" / "password-policy.yaml"
            target.parent.mkdir(parents=True)
            target.write_text(yaml.safe_dump(
                {"password_policy": {
                    "min_length": 12,
                    "require_classes": 4,
                    "history_len": 5,
                }}))
            cfg = PasswordPolicyConfig(Path(d))
            v = cfg.load_values()
        self.assertTrue(v["require_special"])
        self.assertEqual(v["require_classes"], 4)

    def test_explicit_booleans_take_precedence_over_legacy_classes(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = PasswordPolicyConfig(Path(d))
            stored = cfg.save_values({
                "require_uppercase": False,
                "require_lowercase": True,
                "require_digit": True,
                "require_special": True,
                # Legacy integer is sent but the booleans win.
                "require_classes": 1,
            })
        self.assertFalse(stored["require_uppercase"])
        self.assertTrue(stored["require_special"])
        # 3 booleans on ⇒ derived count = 3
        self.assertEqual(stored["require_classes"], 3)

    def test_lockout_clamps_out_of_range(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = PasswordPolicyConfig(Path(d))
            stored = cfg.save_values({
                "max_age_days": -10,           # below floor
                "lockout_threshold": 9999,     # above ceiling
                "lockout_window_minutes": -5,  # below floor
            })
        self.assertEqual(stored["max_age_days"], 0)
        self.assertEqual(stored["lockout_threshold"], 50)
        self.assertEqual(stored["lockout_window_minutes"], 0)

    def test_build_policy_uses_derived_class_count(self):
        """``build_policy()`` constructs a PasswordPolicy from the
        derived ``require_classes`` integer, so toggling booleans
        actually affects strength enforcement."""
        with tempfile.TemporaryDirectory() as d:
            cfg = PasswordPolicyConfig(Path(d))
            cfg.save_values({
                "min_length": 12,
                "require_uppercase": True,
                "require_lowercase": True,
                "require_digit": True,
                "require_special": True,
            })
            pol = cfg.build_policy()
        self.assertEqual(pol.required_classes, 4)
        # No-special candidate must fail under all-4-classes policy.
        self.assertFalse(
            pol.check_candidate("LongPassword123").ok,
            "all-4-classes policy must reject a no-symbol candidate",
        )


if __name__ == "__main__":
    unittest.main()
