"""Tests for AutheliaIPDenyProvider.

Uses real tempdir + yaml files so we exercise the actual
SafeYamlEditor round-trip (malformed yaml catches regressions that
pure-python mocks would miss).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.users.ip_deny import (  # noqa: E402
    IPDeny,
    IPDenyProvider,
)
from media_stack.services.apps.authelia.ip_deny_provider import (  # noqa: E402
    AutheliaIPDenyError,
    AutheliaIPDenyProvider,
    _find_managed_rule,
    _is_managed_rule,
    _merge_deny,
)


def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


class AutheliaIPDenyProtocolTests(unittest.TestCase):

    def test_satisfies_ip_deny_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = AutheliaIPDenyProvider(Path(tmp) / "configuration.yml")
            self.assertIsInstance(p, IPDenyProvider)

    def test_has_expected_name(self) -> None:
        self.assertEqual(
            AutheliaIPDenyProvider(Path("/x")).name, "authelia",
        )


class AutheliaIPDenyListTests(unittest.TestCase):

    def test_empty_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = AutheliaIPDenyProvider(
                Path(tmp) / "does_not_exist.yml",
            )
            self.assertEqual(p.list_ip_denies(), [])

    def test_empty_when_no_access_control(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "configuration.yml"
            _write_yaml(path, {"jwt_secret": "x"})
            p = AutheliaIPDenyProvider(path)
            self.assertEqual(p.list_ip_denies(), [])

    def test_empty_when_access_control_has_no_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "configuration.yml"
            _write_yaml(path, {"access_control": {"default_policy": "deny"}})
            p = AutheliaIPDenyProvider(path)
            self.assertEqual(p.list_ip_denies(), [])

    def test_empty_when_only_admin_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "configuration.yml"
            _write_yaml(path, {
                "access_control": {
                    "default_policy": "deny",
                    "rules": [
                        {"domain": "media.example",
                         "policy": "one_factor",
                         "subject": "group:family"},
                    ],
                },
            })
            p = AutheliaIPDenyProvider(path)
            self.assertEqual(p.list_ip_denies(), [])

    def test_lists_managed_cidrs_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "configuration.yml"
            _write_yaml(path, {
                "access_control": {
                    "default_policy": "deny",
                    "rules": [
                        {"domain": "*", "policy": "deny",
                         "networks": ["203.0.113.45/32",
                                      "198.51.100.0/24"]},
                        {"domain": "media.example",
                         "policy": "one_factor",
                         "subject": "group:family"},
                    ],
                },
            })
            p = AutheliaIPDenyProvider(path)
            result = p.list_ip_denies()
            self.assertEqual(
                [d.cidr for d in result],
                ["203.0.113.45/32", "198.51.100.0/24"],
            )


class AutheliaIPDenyAddTests(unittest.TestCase):

    def _base_config(self) -> dict:
        return {
            "access_control": {
                "default_policy": "deny",
                "rules": [
                    {"domain": "media.example",
                     "policy": "one_factor",
                     "subject": "group:family"},
                ],
            },
        }

    def test_add_creates_managed_rule_at_position_0(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "configuration.yml"
            _write_yaml(path, self._base_config())
            p = AutheliaIPDenyProvider(path)
            p.add_ip_deny(IPDeny(cidr="203.0.113.45"))
            data = _read_yaml(path)
            rules = data["access_control"]["rules"]
            self.assertEqual(rules[0], {
                "domain": "*",
                "policy": "deny",
                "networks": ["203.0.113.45/32"],
            })
            # Existing admin rule preserved.
            self.assertEqual(rules[1]["domain"], "media.example")

    def test_add_appends_to_existing_managed_rule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "configuration.yml"
            _write_yaml(path, self._base_config())
            p = AutheliaIPDenyProvider(path)
            p.add_ip_deny(IPDeny(cidr="203.0.113.45"))
            p.add_ip_deny(IPDeny(cidr="198.51.100.0/24"))
            data = _read_yaml(path)
            networks = data["access_control"]["rules"][0]["networks"]
            self.assertEqual(
                networks, ["203.0.113.45/32", "198.51.100.0/24"],
            )

    def test_add_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "configuration.yml"
            _write_yaml(path, self._base_config())
            p = AutheliaIPDenyProvider(path)
            p.add_ip_deny(IPDeny(cidr="203.0.113.45"))
            p.add_ip_deny(IPDeny(cidr="203.0.113.45"))
            data = _read_yaml(path)
            networks = data["access_control"]["rules"][0]["networks"]
            self.assertEqual(networks, ["203.0.113.45/32"])

    def test_add_normalises_bare_address_to_slash32(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "configuration.yml"
            _write_yaml(path, self._base_config())
            p = AutheliaIPDenyProvider(path)
            p.add_ip_deny(IPDeny(cidr="203.0.113.45"))
            data = _read_yaml(path)
            networks = data["access_control"]["rules"][0]["networks"]
            self.assertEqual(networks, ["203.0.113.45/32"])

    def test_add_into_empty_file_creates_structure(self) -> None:
        # Fresh config file with no access_control at all — writer
        # should create the mapping and add the rule.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "configuration.yml"
            _write_yaml(path, {"jwt_secret": "x"})
            p = AutheliaIPDenyProvider(path)
            p.add_ip_deny(IPDeny(cidr="203.0.113.45"))
            data = _read_yaml(path)
            self.assertIn("access_control", data)
            self.assertEqual(
                data["access_control"]["rules"][0]["networks"],
                ["203.0.113.45/32"],
            )

    def test_add_fires_reload_hook(self) -> None:
        hook = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "configuration.yml"
            _write_yaml(path, self._base_config())
            p = AutheliaIPDenyProvider(path, reload_hook=hook)
            p.add_ip_deny(IPDeny(cidr="203.0.113.45"))
        hook.assert_called_once_with()

    def test_add_without_reload_hook_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "configuration.yml"
            _write_yaml(path, self._base_config())
            p = AutheliaIPDenyProvider(path, reload_hook=None)
            p.add_ip_deny(IPDeny(cidr="203.0.113.45"))


class AutheliaIPDenyRemoveTests(unittest.TestCase):

    def _config_with_bans(self, cidrs: list[str]) -> dict:
        return {
            "access_control": {
                "default_policy": "deny",
                "rules": [
                    {"domain": "*", "policy": "deny",
                     "networks": list(cidrs)},
                    {"domain": "media.example",
                     "policy": "one_factor",
                     "subject": "group:family"},
                ],
            },
        }

    def test_remove_drops_cidr_preserves_others(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "configuration.yml"
            _write_yaml(
                path,
                self._config_with_bans(
                    ["203.0.113.45/32", "198.51.100.0/24"],
                ),
            )
            p = AutheliaIPDenyProvider(path)
            p.remove_ip_deny("203.0.113.45/32")
            data = _read_yaml(path)
            self.assertEqual(
                data["access_control"]["rules"][0]["networks"],
                ["198.51.100.0/24"],
            )

    def test_remove_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "configuration.yml"
            _write_yaml(
                path, self._config_with_bans(["203.0.113.45/32"]),
            )
            p = AutheliaIPDenyProvider(path)
            p.remove_ip_deny("10.0.0.0/8")  # never banned — no-op
            data = _read_yaml(path)
            self.assertEqual(
                data["access_control"]["rules"][0]["networks"],
                ["203.0.113.45/32"],
            )

    def test_remove_last_cidr_drops_managed_rule_entirely(self) -> None:
        # Empty networks would be rejected by Authelia's validator,
        # so the rule must be removed entirely when the list empties.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "configuration.yml"
            _write_yaml(
                path, self._config_with_bans(["203.0.113.45/32"]),
            )
            p = AutheliaIPDenyProvider(path)
            p.remove_ip_deny("203.0.113.45/32")
            data = _read_yaml(path)
            rules = data["access_control"]["rules"]
            # Only the admin rule remains.
            self.assertEqual(len(rules), 1)
            self.assertEqual(rules[0]["domain"], "media.example")

    def test_remove_normalises_bare_address(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "configuration.yml"
            _write_yaml(
                path, self._config_with_bans(["203.0.113.45/32"]),
            )
            p = AutheliaIPDenyProvider(path)
            p.remove_ip_deny("203.0.113.45")  # bare form
            data = _read_yaml(path)
            rules = data["access_control"]["rules"]
            self.assertEqual(len(rules), 1)
            self.assertEqual(rules[0]["domain"], "media.example")

    def test_remove_fires_reload_hook(self) -> None:
        hook = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "configuration.yml"
            _write_yaml(
                path, self._config_with_bans(["203.0.113.45/32"]),
            )
            p = AutheliaIPDenyProvider(path, reload_hook=hook)
            p.remove_ip_deny("203.0.113.45/32")
        hook.assert_called_once_with()


class AutheliaIPDenyMultipleManagedRulesTests(unittest.TestCase):

    def test_list_raises_on_multiple_managed_rules(self) -> None:
        # A human edited the config and there are now TWO candidate
        # managed rules. Refuse to guess.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "configuration.yml"
            _write_yaml(path, {
                "access_control": {
                    "default_policy": "deny",
                    "rules": [
                        {"domain": "*", "policy": "deny",
                         "networks": ["203.0.113.45/32"]},
                        {"domain": "*", "policy": "deny",
                         "networks": ["198.51.100.0/24"]},
                    ],
                },
            })
            p = AutheliaIPDenyProvider(path)
            with self.assertRaises(AutheliaIPDenyError):
                p.list_ip_denies()


class AutheliaIPDenyManagedRuleDetectionTests(unittest.TestCase):
    """Unit-test the managed-rule detector — these are the invariants
    the whole module rests on."""

    def test_canonical_managed_rule_detected(self) -> None:
        self.assertTrue(_is_managed_rule({
            "domain": "*", "policy": "deny", "networks": [],
        }))

    def test_allow_policy_not_managed(self) -> None:
        self.assertFalse(_is_managed_rule({
            "domain": "*", "policy": "one_factor", "networks": [],
        }))

    def test_specific_domain_not_managed(self) -> None:
        self.assertFalse(_is_managed_rule({
            "domain": "media.example", "policy": "deny",
            "networks": [],
        }))

    def test_extra_keys_not_managed(self) -> None:
        # Any additional key (subject, resources, methods, ...)
        # disqualifies the rule from being our managed one — we only
        # touch rules we created.
        self.assertFalse(_is_managed_rule({
            "domain": "*", "policy": "deny", "networks": [],
            "subject": "group:family",
        }))

    def test_missing_networks_not_managed(self) -> None:
        self.assertFalse(_is_managed_rule({
            "domain": "*", "policy": "deny",
        }))

    def test_networks_non_list_not_managed(self) -> None:
        self.assertFalse(_is_managed_rule({
            "domain": "*", "policy": "deny", "networks": "203.0.113.0/24",
        }))

    def test_non_dict_not_managed(self) -> None:
        self.assertFalse(_is_managed_rule("a string"))
        self.assertFalse(_is_managed_rule(None))
        self.assertFalse(_is_managed_rule([]))

    def test_find_returns_none_on_no_access_control(self) -> None:
        self.assertIsNone(_find_managed_rule({}))

    def test_find_returns_none_on_empty_rules(self) -> None:
        self.assertIsNone(
            _find_managed_rule({"access_control": {"rules": []}}),
        )

    def test_find_returns_view_with_index_and_networks(self) -> None:
        view = _find_managed_rule({"access_control": {"rules": [
            {"domain": "media.example",
             "policy": "one_factor",
             "subject": "group:family"},
            {"domain": "*", "policy": "deny",
             "networks": ["203.0.113.45/32", "198.51.100.0/24"]},
        ]}})
        self.assertIsNotNone(view)
        assert view is not None  # for type-checker
        self.assertEqual(view.index, 1)
        self.assertEqual(
            view.networks, ("203.0.113.45/32", "198.51.100.0/24"),
        )


class AutheliaIPDenyMergeHelperTests(unittest.TestCase):
    """Pure-function tests for ``_merge_deny``."""

    def test_requires_add_or_remove(self) -> None:
        with self.assertRaises(ValueError):
            _merge_deny({}, add=None, remove=None)

    def test_add_from_empty_creates_rule_at_position_0(self) -> None:
        result = _merge_deny(
            {"access_control": {"rules": [
                {"domain": "media.example", "policy": "one_factor",
                 "subject": "group:family"},
            ]}},
            add="203.0.113.45/32", remove=None,
        )
        rules = result["access_control"]["rules"]
        self.assertEqual(rules[0]["domain"], "*")
        self.assertEqual(
            rules[0]["networks"], ["203.0.113.45/32"],
        )
        self.assertEqual(rules[1]["domain"], "media.example")

    def test_remove_last_cidr_drops_rule(self) -> None:
        result = _merge_deny(
            {"access_control": {"rules": [
                {"domain": "*", "policy": "deny",
                 "networks": ["203.0.113.45/32"]},
            ]}},
            add=None, remove="203.0.113.45/32",
        )
        self.assertEqual(result["access_control"]["rules"], [])

    def test_raises_on_duplicate_managed_rules(self) -> None:
        with self.assertRaises(AutheliaIPDenyError):
            _merge_deny(
                {"access_control": {"rules": [
                    {"domain": "*", "policy": "deny",
                     "networks": ["a/32"]},
                    {"domain": "*", "policy": "deny",
                     "networks": ["b/32"]},
                ]}},
                add="c/32", remove=None,
            )

    def test_input_not_mutated(self) -> None:
        original = {"access_control": {"rules": [
            {"domain": "media.example", "policy": "one_factor",
             "subject": "group:family"},
        ]}}
        _merge_deny(original, add="203.0.113.45/32", remove=None)
        # Caller's dict must be intact — SafeYamlEditor relies on
        # the mutator being pure.
        self.assertEqual(len(original["access_control"]["rules"]), 1)
        self.assertEqual(
            original["access_control"]["rules"][0]["domain"],
            "media.example",
        )


if __name__ == "__main__":
    unittest.main()
