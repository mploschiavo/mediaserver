import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from cli.bootstrap_component_resolver import (  # noqa: E402
    evaluate_phase_condition,
    resolve_bootstrap_all_components,
    resolve_bootstrap_all_phase_plan,
    resolve_bootstrap_job_phase_plan,
    resolve_bootstrap_component_plan,
    resolve_bootstrap_enable_workers,
    resolve_phase_skip_flag_specs,
    resolve_runner_phase_script,
    resolve_worker_deployment_name,
    resolve_worker_manifest_path,
)
from core.exceptions import ConfigError  # noqa: E402


class BootstrapComponentResolverTests(unittest.TestCase):
    def _base_config(self) -> dict:
        return json.loads(
            (ROOT / "bootstrap" / "media-stack.bootstrap.json").read_text(encoding="utf-8")
        )

    def _write_config(self, payload: dict) -> Path:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        path = Path(tmpdir.name) / "bootstrap.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def test_resolve_plan_uses_configured_bindings_and_scale_policy_apps(self):
        cfg = self._base_config()
        hooks = dict(cfg.get("adapter_hooks") or {})
        hooks["scale_policy"] = {
            "core_apps": ["jf", "seerr", "qbit", "sab", "prowlarr", "bazarr", "maintainerr"],
            "worker_apps": ["unpackerr"],
        }
        hooks["bootstrap_all"] = {"enable_workers": ["unpackerr"]}
        cfg["adapter_hooks"] = hooks
        cfg["technology_bindings"] = {
            "torrent_client": "qbit",
            "usenet_client": "sab",
            "media_server": "jf",
            "request_manager": "seerr",
        }

        plan = resolve_bootstrap_component_plan(self._write_config(cfg))

        self.assertEqual(plan.role_bindings["torrent_client"], "qbittorrent")
        self.assertEqual(plan.role_bindings["usenet_client"], "sabnzbd")
        self.assertEqual(plan.role_bindings["media_server"], "jellyfin")
        self.assertEqual(plan.role_bindings["request_manager"], "jellyseerr")

        self.assertEqual(
            plan.core_apps,
            (
                "jellyfin",
                "jellyseerr",
                "qbittorrent",
                "sabnzbd",
                "prowlarr",
                "bazarr",
                "maintainerr",
            ),
        )
        self.assertEqual(plan.worker_apps, ("unpackerr",))

    def test_explicit_scale_policy_overrides_core_and_worker_sets(self):
        cfg = self._base_config()
        hooks = dict(cfg.get("adapter_hooks") or {})
        hooks["scale_policy"] = {
            "core_apps": ["radarr", "Custom Core"],
            "worker_apps": ["Worker-X"],
        }
        cfg["adapter_hooks"] = hooks

        plan = resolve_bootstrap_component_plan(self._write_config(cfg))
        self.assertEqual(plan.core_apps, ("radarr", "custom-core"))
        self.assertEqual(plan.worker_apps, ("worker-x",))

    def test_missing_scale_policy_lists_raise_config_error(self):
        cfg = self._base_config()
        hooks = dict(cfg.get("adapter_hooks") or {})
        hooks["scale_policy"] = {}
        cfg["adapter_hooks"] = hooks

        with self.assertRaises(ConfigError):
            resolve_bootstrap_component_plan(self._write_config(cfg))

    def test_runner_phase_script_resolution_uses_specific_and_wildcard(self):
        cfg = {
            "adapter_hooks": {
                "runner_phase_scripts": {
                    "torrent_client_credentials": {
                        "qbittorrent": "ensure-qbit-credentials.sh",
                        "*": "fallback.sh",
                    }
                }
            }
        }
        aliases = {"qbit": "qbittorrent", "qbittorrent": "qbittorrent"}

        direct = resolve_runner_phase_script(
            cfg,
            phase_key="torrent_client_credentials",
            technology="qbit",
            aliases=aliases,
        )
        fallback = resolve_runner_phase_script(
            cfg,
            phase_key="torrent_client_credentials",
            technology="transmission",
            aliases=aliases,
        )

        self.assertEqual(direct, "ensure-qbit-credentials.sh")
        self.assertEqual(fallback, "fallback.sh")

    def test_bootstrap_enable_workers_explicit_or_fallback(self):
        aliases = {"unpackerr": "unpackerr", "x": "x"}
        explicit_cfg = {"adapter_hooks": {"bootstrap_all": {"enable_workers": ["X"]}}}
        fallback_cfg = {"adapter_hooks": {}}

        self.assertEqual(
            resolve_bootstrap_enable_workers(
                explicit_cfg, aliases=aliases, fallback_workers=("unpackerr",)
            ),
            ("x",),
        )
        self.assertEqual(
            resolve_bootstrap_enable_workers(
                fallback_cfg, aliases=aliases, fallback_workers=("unpackerr",)
            ),
            ("unpackerr",),
        )

    def test_worker_manifest_and_deployment_mappings_allow_overrides(self):
        cfg = {
            "adapter_hooks": {
                "bootstrap_all": {
                    "worker_manifests": {"unpackerr": "k8s/custom-unpackerr.yaml"},
                    "worker_deployments": {"unpackerr": "unpackerr-worker"},
                }
            }
        }
        aliases = {"unpackerr": "unpackerr"}

        self.assertEqual(
            resolve_worker_manifest_path(cfg, worker="unpackerr", aliases=aliases),
            "k8s/custom-unpackerr.yaml",
        )
        self.assertEqual(
            resolve_worker_deployment_name(cfg, worker="unpackerr", aliases=aliases),
            "unpackerr-worker",
        )

    def test_bootstrap_phase_plan_order_is_config_driven(self):
        cfg = {
            "adapter_hooks": {
                "bootstrap_all": {
                    "phase_plan": [
                        {"operation": "run_script"},
                        {"operation": "run_component_script"},
                    ]
                },
                "bootstrap_job": {
                    "phase_plan": [
                        {"operation": "resolve_bootstrap_config"},
                        {"operation": "ensure_bootstrap_pvc_prereqs"},
                    ]
                },
            }
        }

        all_plan = resolve_bootstrap_all_phase_plan(cfg)
        job_plan = resolve_bootstrap_job_phase_plan(cfg)
        self.assertEqual(
            [step.operation for step in all_plan],
            ["run_script", "run_component_script"],
        )
        self.assertEqual(
            [step.operation for step in job_plan],
            ["resolve_bootstrap_config", "ensure_bootstrap_pvc_prereqs"],
        )

    def test_resolve_bootstrap_all_components_prefers_declared_components_map(self):
        cfg = {
            "adapter_hooks": {
                "bootstrap_all": {
                    "components": {
                        "download": {"binding": "torrent_client"},
                        "indexer": {"technology": "Prowlarr"},
                        "requests": "jellyseerr",
                    }
                }
            }
        }
        aliases = {
            "qbittorrent": "qbittorrent",
            "prowlarr": "prowlarr",
            "jellyseerr": "jellyseerr",
        }
        role_bindings = {"torrent_client": "qbittorrent", "request_manager": "jellyseerr"}

        resolved = resolve_bootstrap_all_components(
            cfg,
            aliases=aliases,
            role_bindings=role_bindings,
        )

        self.assertEqual(
            resolved,
            {
                "download": "qbittorrent",
                "indexer": "prowlarr",
                "requests": "jellyseerr",
            },
        )

    def test_resolve_phase_skip_flag_specs_includes_generic_and_configured_aliases(self):
        specs = resolve_phase_skip_flag_specs(self._base_config(), pipeline="bootstrap_all")
        by_key = {spec.key: spec for spec in specs}
        torrent_spec = by_key["skip_torrent_client_ensure"]
        self.assertIn("--skip-torrent-client-ensure", torrent_spec.option_strings)
        self.assertIn("--skip-qbit-ensure", torrent_spec.option_strings)
        self.assertIn("SKIP_TORRENT_CLIENT_ENSURE", torrent_spec.env_vars)
        self.assertIn("SKIP_QBIT_ENSURE", torrent_spec.env_vars)

    def test_phase_condition_evaluator_supports_boolean_logic_and_path_lookups(self):
        context = {"checks": {"ready": True}, "bindings": {"torrent_client": "qbittorrent"}}
        condition = {
            "all_of": [
                {"var": "checks.ready", "equals": True},
                {"var": "bindings.torrent_client", "in": ["qbittorrent", "transmission"]},
            ]
        }
        self.assertTrue(evaluate_phase_condition(condition, context=context))
        self.assertFalse(
            evaluate_phase_condition(
                {"var": "bindings.usenet_client", "exists": True},
                context=context,
            )
        )


if __name__ == "__main__":
    unittest.main()
