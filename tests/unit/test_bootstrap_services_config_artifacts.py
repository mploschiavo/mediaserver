import json
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

    def test_ensure_maintainerr_policy_uses_default_rule_library(self):
        svc = _service()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {
                "maintainerr": {
                    "enabled": True,
                    "policy_relative_path": "maintainerr/policy.json",
                    "rules_library": {
                        "enabled": True,
                        "include_defaults": True,
                        "relative_path": "maintainerr/rules",
                        "merge_mode": "append",
                    },
                    "policy": {},
                }
            }
            svc.ensure_maintainerr_policy(cfg, tmp)
            path = Path(tmp) / "maintainerr" / "policy.json"
            self.assertTrue(path.exists())
            rendered = json.loads(path.read_text(encoding="utf-8"))
            by_name = {
                str(rule.get("name") or ""): rule for rule in (rendered.get("rules") or [])
            }

            expected_rule_names = {
                "Protect Favorited Media",
                "Delete Watched Movies After 30 Days",
                "Delete Watched TV After 30 Days",
                "Delete Played Music After 30 Days",
                "Delete Read Books After 30 Days",
                "Remove Old Requested Unwatched Content",
                "Unmonitor Unwatched TV After 180 Days",
                "Leaving Soon (5 Day Warning)",
            }
            self.assertTrue(expected_rule_names.issubset(set(by_name.keys())))

            self.assertEqual(
                int(
                    (
                        (by_name["Delete Watched Movies After 30 Days"].get("conditions") or {})
                    ).get("added_days_ago_gte", 0)
                ),
                30,
            )
            self.assertEqual(
                int(
                    (
                        (by_name["Delete Watched TV After 30 Days"].get("conditions") or {})
                    ).get("added_days_ago_gte", 0)
                ),
                30,
            )
            self.assertEqual(
                int(
                    (
                        (
                            by_name["Remove Old Requested Unwatched Content"].get("conditions")
                            or {}
                        )
                    ).get("requested_days_ago_gte", 0)
                ),
                90,
            )
            self.assertEqual(
                int(
                    (
                        (
                            by_name["Unmonitor Unwatched TV After 180 Days"].get("conditions")
                            or {}
                        )
                    ).get("last_watched_days_ago_gte", 0)
                ),
                180,
            )
            self.assertEqual(
                int(
                    (
                        (
                            by_name["Leaving Soon (5 Day Warning)"].get("actions") or {}
                        )
                    ).get("collection_days_before_delete", 0)
                ),
                5,
            )

    def test_ensure_maintainerr_policy_merges_custom_rules_by_name(self):
        svc = _service()
        with tempfile.TemporaryDirectory() as tmp:
            custom_rules_dir = Path(tmp) / "maintainerr" / "rules"
            custom_rules_dir.mkdir(parents=True, exist_ok=True)
            (custom_rules_dir / "10-movies-delete-watched-after-30d.json").write_text(
                json.dumps(
                    {
                        "enabled": True,
                        "rule": {
                            "name": "Delete Watched Movies After 30 Days",
                            "libraries": ["Movies"],
                            "conditions": {"watched": True, "added_days_ago_gte": 45},
                            "actions": {"delete_item": True},
                        },
                    }
                ),
                encoding="utf-8",
            )
            (custom_rules_dir / "99-custom-rule.json").write_text(
                json.dumps(
                    {
                        "enabled": True,
                        "rule": {
                            "name": "Custom Keep Classic Films",
                            "libraries": ["Movies"],
                            "conditions": {"community_rating_gte": 8.0},
                            "actions": {"protect_item": True},
                        },
                    }
                ),
                encoding="utf-8",
            )

            cfg = {
                "maintainerr": {
                    "enabled": True,
                    "rules_library": {
                        "enabled": True,
                        "include_defaults": True,
                        "relative_path": "maintainerr/rules",
                        "merge_mode": "append",
                    },
                    "policy": {},
                }
            }
            svc.ensure_maintainerr_policy(cfg, tmp)
            rendered = json.loads(
                (Path(tmp) / "maintainerr" / "policy.json").read_text(encoding="utf-8")
            )
            by_name = {str(rule.get("name") or ""): rule for rule in (rendered.get("rules") or [])}
            self.assertEqual(
                int(
                    ((by_name["Delete Watched Movies After 30 Days"].get("conditions") or {}).get(
                        "added_days_ago_gte",
                        0,
                    ))
                ),
                45,
            )
            self.assertIn("Custom Keep Classic Films", by_name)

    def test_ensure_maintainerr_policy_replace_mode_uses_only_custom_library(self):
        svc = _service()
        with tempfile.TemporaryDirectory() as tmp:
            custom_rules_dir = Path(tmp) / "maintainerr" / "rules"
            custom_rules_dir.mkdir(parents=True, exist_ok=True)
            (custom_rules_dir / "custom-only.json").write_text(
                json.dumps(
                    {
                        "enabled": True,
                        "rule": {
                            "name": "Custom Only Rule",
                            "libraries": ["Movies"],
                            "conditions": {"watched": False},
                            "actions": {"protect_item": True},
                        },
                    }
                ),
                encoding="utf-8",
            )

            cfg = {
                "maintainerr": {
                    "enabled": True,
                    "rules_library": {
                        "enabled": True,
                        "include_defaults": True,
                        "relative_path": "maintainerr/rules",
                        "merge_mode": "replace",
                    },
                    "policy": {},
                }
            }
            svc.ensure_maintainerr_policy(cfg, tmp)
            rendered = json.loads(
                (Path(tmp) / "maintainerr" / "policy.json").read_text(encoding="utf-8")
            )
            rules = rendered.get("rules") or []
            self.assertEqual(len(rules), 1)
            self.assertEqual(str(rules[0].get("name") or ""), "Custom Only Rule")


if __name__ == "__main__":
    unittest.main()
