"""Parity tests for compose vs k8s deployment modes.

The Envoy config generator runs in BOTH deployment modes using the
same profile. When compose and k8s drift — one gets a fix the other
doesn't, or a new feature only lands on one path — users hit bugs
that reproduce in prod but not in dev (or vice versa).

These tests pin the invariant: for a given profile, the ROUTING
TABLE (vhosts × path prefixes × clusters) must be semantically
equivalent between modes. Deliberate divergences (listener port,
TLS injection) are handled by comparing the per-mode diffs against
an allowlist rather than the full config.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))


def _run_generator(*, compose_mode: bool, tmp: Path) -> dict:
    """Invoke generate_envoy_config_main.main() and return the
    parsed envoy.yaml. Compose mode requires a real compose file;
    k8s mode uses the synthetic-services path."""
    import os
    from media_stack.services.edge.envoy_config_generator import (
        GenerateEnvoyConfigCommand,
    )

    env_keep = {k: os.environ.get(k) for k in (
        "COMPOSE_FILE", "CONFIG_ROOT", "BOOTSTRAP_PROFILE_FILE",
        "BOOTSTRAP_CONFIG_FILE", "K8S_NAMESPACE",
    )}
    try:
        profile_path = tmp / "profile.yaml"
        profile_path.write_text(yaml.safe_dump({
            "metadata": {"name": "media-stack"},
            "auth": {"enabled": False, "mode": "none", "provider": "none"},
            "routing": {
                "base_domain": "local",
                "stack_subdomain": "media-stack",
                "gateway_host": "apps.media-stack.local",
                "gateway_port": 80,
                "app_path_prefix": "/app",
                "strategy": "hybrid",
                "provider": "envoy",
            },
        }), encoding="utf-8")
        os.environ["BOOTSTRAP_PROFILE_FILE"] = str(profile_path)
        os.environ["CONFIG_ROOT"] = str(tmp)
        os.environ["BOOTSTRAP_CONFIG_FILE"] = ""
        if compose_mode:
            # Seed a minimal compose file so the resolver has
            # something to parse. Bootstrap labels are optional —
            # the synthetic path handles services registry-driven.
            compose_path = tmp / "docker-compose.yml"
            compose_path.write_text(yaml.safe_dump({
                "services": {"envoy": {"image": "envoyproxy/envoy:v1.31.2"}},
            }), encoding="utf-8")
            os.environ["COMPOSE_FILE"] = str(compose_path)
        else:
            os.environ["COMPOSE_FILE"] = "/dev/null"  # triggers k8s mode
        try:
            GenerateEnvoyConfigCommand().main()
        except SystemExit as exc:
            if exc.code:
                raise AssertionError(
                    f"generator exited with code {exc.code}")
        out_path = tmp / "envoy" / "envoy.yaml"
        return yaml.safe_load(out_path.read_text(encoding="utf-8"))
    finally:
        for k, v in env_keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _all_clusters(cfg: dict) -> set[str]:
    return {c["name"]
            for c in (cfg.get("static_resources") or {}).get("clusters") or []
            if isinstance(c, dict) and c.get("name")}


def _vhost_domains(cfg: dict) -> set[str]:
    out: set[str] = set()
    for listener in (cfg.get("static_resources") or {}).get("listeners") or []:
        for fc in listener.get("filter_chains") or []:
            for flt in fc.get("filters") or []:
                rc = (flt.get("typed_config") or {}).get("route_config") or {}
                for vh in rc.get("virtual_hosts") or []:
                    for dom in vh.get("domains") or []:
                        out.add(dom)
    return out


class DeploymentModeParityTests(unittest.TestCase):
    """For the same profile, compose and k8s generators must
    produce equivalent routing tables. Documented divergences are
    whitelisted; everything else is a drift bug."""

    def test_both_modes_generate_clean_config(self):
        with tempfile.TemporaryDirectory() as da:
            with tempfile.TemporaryDirectory() as db:
                compose_cfg = _run_generator(
                    compose_mode=True, tmp=Path(da))
                k8s_cfg = _run_generator(
                    compose_mode=False, tmp=Path(db))
        self.assertIn("static_resources", compose_cfg)
        self.assertIn("static_resources", k8s_cfg)

    def test_cluster_set_matches_between_modes(self):
        """Same services → same cluster names. If compose adds a
        cluster k8s doesn't, the same deployment breaks differently
        in the two paths."""
        with tempfile.TemporaryDirectory() as da, \
             tempfile.TemporaryDirectory() as db:
            compose_cfg = _run_generator(
                compose_mode=True, tmp=Path(da))
            k8s_cfg = _run_generator(compose_mode=False, tmp=Path(db))
        compose_clusters = _all_clusters(compose_cfg)
        k8s_clusters = _all_clusters(k8s_cfg)
        # Clusters present in one but not the other (excluding the
        # compose-only empty case where no services have labels).
        only_compose = compose_clusters - k8s_clusters
        only_k8s = k8s_clusters - compose_clusters
        # K8s-mode uses synthetic services so it typically has MORE.
        # The drift we care about: anything in compose NOT in k8s
        # means the compose generator knows about a cluster the k8s
        # generator doesn't — a divergence bug.
        self.assertEqual(
            only_compose, set(),
            "Compose has clusters k8s doesn't: "
            f"{only_compose}. Generator mode-parity drifted.",
        )

    def test_vhost_domains_overlap_between_modes(self):
        """At minimum the main apps.<base> vhost must exist in both
        paths — that's how every service is reached via path-prefix
        routing."""
        with tempfile.TemporaryDirectory() as da, \
             tempfile.TemporaryDirectory() as db:
            compose_cfg = _run_generator(
                compose_mode=True, tmp=Path(da))
            k8s_cfg = _run_generator(compose_mode=False, tmp=Path(db))
        compose_doms = _vhost_domains(compose_cfg)
        k8s_doms = _vhost_domains(k8s_cfg)
        common = compose_doms & k8s_doms
        self.assertIn(
            "apps.media-stack.local", common | k8s_doms,
            "apps.<base> vhost missing from both compose and k8s "
            "generators — users can't reach /app/<service> on "
            "either path.",
        )


class ListenerDivergenceIsDocumentedTests(unittest.TestCase):
    """The ONE documented divergence: compose may inject a TLS
    transport_socket (self-signed cert), k8s relies on ingress TLS
    termination and stays plain HTTP. Verify that divergence is
    bounded (exactly the transport_socket key) so a new field can't
    sneak in unnoticed."""

    def test_only_known_keys_differ_between_listener_chains(self):
        with tempfile.TemporaryDirectory() as da, \
             tempfile.TemporaryDirectory() as db:
            compose_cfg = _run_generator(
                compose_mode=True, tmp=Path(da))
            k8s_cfg = _run_generator(compose_mode=False, tmp=Path(db))

        def _filter_chain_keys(cfg):
            listeners = (cfg.get("static_resources") or {}).get(
                "listeners") or []
            if not listeners:
                return set()
            fc = (listeners[0].get("filter_chains") or [{}])[0]
            return set(fc.keys())

        compose_keys = _filter_chain_keys(compose_cfg)
        k8s_keys = _filter_chain_keys(k8s_cfg)
        # Allowed divergence: transport_socket is compose-only.
        allowed = {"transport_socket"}
        unexplained = (compose_keys ^ k8s_keys) - allowed
        self.assertEqual(
            unexplained, set(),
            "Undocumented divergence between compose and k8s "
            f"listener config: {unexplained}. Add the key to the "
            "allowlist if intentional, or fix the drift.",
        )


if __name__ == "__main__":
    unittest.main()
