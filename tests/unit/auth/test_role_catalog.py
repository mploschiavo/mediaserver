"""Tests for the role catalog and policy mapper."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.users.role_catalog import RoleCatalog  # noqa: E402
from media_stack.core.auth.users.role_policy_mapper import RolePolicyMapper  # noqa: E402

_CONTRACTS_ROLES = ROOT / "contracts" / "roles.yaml"


def _write_catalog(path: Path, roles: dict) -> None:
    path.write_text(yaml.safe_dump({"version": 1, "roles": roles}))


class RoleCatalogTests(unittest.TestCase):
    def test_loads_real_contracts_roles_file(self):
        # Sanity: the shipped contracts/roles.yaml must parse and include all
        # roles the UI exposes in the add-user dialog.
        catalog = RoleCatalog(_CONTRACTS_ROLES)
        slugs = set(catalog.slugs())
        for expected in ("superadmin", "family_admin", "adult", "teen", "kid", "guest"):
            self.assertIn(expected, slugs)

    def test_require_raises_on_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "roles.yaml"
            _write_catalog(path, {"adult": {"name": "Adult"}})
            c = RoleCatalog(path)
            with self.assertRaises(KeyError):
                c.require("nope")

    def test_empty_file_yields_no_roles(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "roles.yaml"
            path.write_text("")
            c = RoleCatalog(path)
            self.assertEqual(c.list_all(), [])

    def test_role_fields_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "roles.yaml"
            _write_catalog(path, {
                "kid": {
                    "name": "Kid",
                    "sso_groups": ["family", "kids"],
                    "provider_payloads": {
                        "jellyfin": {"MaxParentalRating": 7},
                        "jellyseerr": {"permissions": 0},
                    },
                },
            })
            c = RoleCatalog(path)
            role = c.require("kid")
            self.assertEqual(role.sso_groups, ["family", "kids"])
            self.assertEqual(role.provider_payloads["jellyfin"]["MaxParentalRating"], 7)

    def test_reload_picks_up_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "roles.yaml"
            _write_catalog(path, {"adult": {"name": "Adult"}})
            c = RoleCatalog(path)
            self.assertEqual(c.slugs(), ["adult"])
            _write_catalog(path, {"adult": {"name": "Adult"}, "teen": {"name": "Teen"}})
            c.reload()
            self.assertIn("teen", c.slugs())


class RolePolicyMapperTests(unittest.TestCase):
    def test_sso_groups_from_role(self):
        catalog = RoleCatalog(_CONTRACTS_ROLES)
        mapper = RolePolicyMapper()
        role = catalog.require("kid")
        self.assertEqual(mapper.sso_groups(role), ["family", "kids"])

    def test_payload_for_known_provider(self):
        catalog = RoleCatalog(_CONTRACTS_ROLES)
        mapper = RolePolicyMapper()
        payload = mapper.payload_for(catalog.require("kid"), "jellyfin")
        self.assertFalse(payload.get("IsAdministrator"))
        self.assertEqual(payload.get("MaxParentalRating"), 7)

    def test_payload_for_unknown_provider_empty(self):
        catalog = RoleCatalog(_CONTRACTS_ROLES)
        mapper = RolePolicyMapper()
        self.assertEqual(
            mapper.payload_for(catalog.require("adult"), "nonexistent"), {},
        )


if __name__ == "__main__":
    unittest.main()
