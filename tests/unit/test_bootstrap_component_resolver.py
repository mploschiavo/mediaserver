import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from cli.bootstrap_component_resolver import (  # noqa: E402
    resolve_bootstrap_component_plan,
    resolve_bootstrap_enable_workers,
    resolve_runner_phase_script,
    resolve_worker_deployment_name,
    resolve_worker_manifest_path,
)


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

    def test_resolve_plan_derives_bindings_and_apps(self):
        cfg = self._base_config()
        hooks = dict(cfg.get("adapter_hooks") or {})
        hooks.pop("scale_policy", None)
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

        for expected in ("qbittorrent", "sabnzbd", "jellyfin", "jellyseerr", "prowlarr"):
            self.assertIn(expected, plan.core_apps)

        self.assertIn("flaresolverr", plan.worker_apps)
        self.assertIn("unpackerr", plan.worker_apps)
        self.assertNotIn("flaresolverr", plan.core_apps)

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


if __name__ == "__main__":
    unittest.main()
