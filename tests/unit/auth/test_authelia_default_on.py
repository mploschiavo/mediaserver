"""Ratchet: Authelia ships ON by default for compose installs.

The 2026-04-21 incident chain:
  - Authelia was gated behind ``profiles: ["auth-authelia"]`` in
    the compose file.
  - The default bootstrap profile said ``auth.enabled: false``.
  - The dashboard wizard talked about SSO as if it existed.
  - End users running ``docker compose up -d`` got a working stack
    with no SSO at all, while the UI implied otherwise.

Fix: drop the profile gate, flip the default profile to
``provider: authelia``, and add a LAN-bypass rule to Authelia's
access_control so the home use-case (couch + iPad) doesn't see a
sign-in screen.

This test pins the four invariants that together make SSO work
out of the box. If any of them regress, the install reverts to
"unusable" — fail loudly here instead of in production.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

_DIST_COMPOSE = ROOT / "dist" / "docker-compose.yml"
_SRC_COMPOSE = ROOT / "docker" / "docker-compose.yml"
_STANDARD_PROFILE = (
    ROOT / "examples" / "bootstrap-profiles" / "media-compose-standard.yaml"
)
_FULL_PROFILE = (
    ROOT / "examples" / "bootstrap-profiles" / "media-compose-full.yaml"
)
_GENERATOR = (
    ROOT / "src" / "media_stack" / "core" / "auth"
    / "authelia_config_generator.py"
)


def _load_compose(path: Path) -> dict:
    if yaml is None:
        raise unittest.SkipTest("PyYAML not installed")
    text = path.read_text(encoding="utf-8")
    # Strip the dist/ header — comments are fine for yaml but the
    # ``services:`` key is what we want.
    return yaml.safe_load(text) or {}


class AutheliaDefaultOnRatchet(unittest.TestCase):

    def setUp(self) -> None:
        if yaml is None:
            self.skipTest("PyYAML not installed")
        for p in (_DIST_COMPOSE, _SRC_COMPOSE, _STANDARD_PROFILE,
                  _FULL_PROFILE, _GENERATOR):
            if not p.is_file():
                self.skipTest(f"missing: {p}")

    # --- Invariant 1: no profile gate on the Authelia service. ---
    def test_authelia_service_has_no_profiles_key(self) -> None:
        for path in (_SRC_COMPOSE, _DIST_COMPOSE):
            doc = _load_compose(path)
            authelia = (doc.get("services") or {}).get("authelia") or {}
            self.assertTrue(
                authelia, f"{path.name} has no `authelia` service",
            )
            self.assertNotIn(
                "profiles", authelia,
                f"{path.name} authelia service is back behind a "
                "compose profile — `docker compose up -d` will skip "
                "it. End users get a stack with no SSO. Drop the "
                "`profiles:` key.",
            )

    # --- Invariant 2: Authentik stays gated (not implemented). ---
    def test_authentik_still_gated_to_avoid_unimplemented_provider(self) -> None:
        """Authentik isn't implemented in this codebase. Leave the
        compose profile gate in place so it doesn't accidentally
        come up alongside Authelia and confuse the user."""
        for name in ("authentik", "authentik-postgresql", "authentik-worker"):
            doc = _load_compose(_DIST_COMPOSE)
            svc = (doc.get("services") or {}).get(name)
            if svc is None:
                continue
            self.assertEqual(
                svc.get("profiles"), ["auth-authentik"],
                f"{name} should stay behind the auth-authentik "
                "compose profile (Authentik isn't implemented)",
            )

    # --- Invariant 3: standard + full profiles claim authelia. ---
    def test_default_bootstrap_profiles_enable_authelia(self) -> None:
        for path in (_STANDARD_PROFILE, _FULL_PROFILE):
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            auth = doc.get("auth") or {}
            self.assertTrue(
                auth.get("enabled"), f"{path.name} auth.enabled must be true",
            )
            self.assertEqual(
                auth.get("provider"), "authelia",
                f"{path.name} auth.provider must be 'authelia' "
                "(matches the compose service that's actually running)",
            )
            self.assertEqual(
                auth.get("mode"), "authelia",
                f"{path.name} auth.mode must be 'authelia' so the "
                "controller wires Envoy ext_authz",
            )

    # --- Invariant 4: Authelia config has LAN bypass. ---
    def test_lan_bypass_rule_in_access_control_generator(self) -> None:
        """The access-control rule that lets RFC 1918 / loopback
        clients skip the SSO challenge entirely. Without this, every
        request from a LAN browser hits the Authelia portal — that's
        the friction we're trying to remove for the home use-case."""
        text = _GENERATOR.read_text(encoding="utf-8")
        # Required CIDR ranges.
        for cidr in ("192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12",
                     "127.0.0.0/8"):
            self.assertIn(
                f'"{cidr}"', text,
                f"_build_access_control no longer references {cidr} — "
                "LAN/loopback bypass rule has regressed.",
            )
        # The rule must use ``policy: bypass`` (not one_factor) for
        # the LAN entry. Easiest reliable check: find the LAN rule
        # block by its first CIDR and assert ``"policy": "bypass"``
        # appears within ~120 chars of it.
        m = re.search(
            r'"192\.168\.0\.0/16"[^}]{0,200}"policy"\s*:\s*"bypass"',
            text, re.DOTALL,
        )
        self.assertIsNotNone(
            m,
            "LAN access_control rule isn't using policy='bypass'. "
            "If it's set to 'one_factor' or 'two_factor', LAN clients "
            "will be challenged for sign-in on every visit — exactly "
            "the friction the rule exists to avoid.",
        )


if __name__ == "__main__":
    unittest.main()
