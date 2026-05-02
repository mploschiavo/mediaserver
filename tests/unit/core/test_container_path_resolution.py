"""Regression tests for container path resolution and service defaults.

Prevents:
1. Auth contract path not found in Docker containers (auth dropdown empty)
2. Homepage services.yaml not generated (tiles missing)
3. Contract files unreachable when src/ is volume-mounted

Run with: python -m pytest tests/unit/test_container_path_resolution.py -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml

# Phase 16-B (gateway_policy moved core/auth -> domain/auth) collapsed
# the parallel _CONTRACT_PATH_REPO and _CONTRACT_PATH_CONTAINER
# constants into a single _resolve_contract_path() candidate-list walk.
# Only the resolved _CONTRACT_PATH survives; the repo-vs-container
# distinction is now expressed inside the resolver, not as separate
# module-level constants.
from media_stack.core.auth.gateway_policy import (
    AuthContractService,
    _CONTRACT_PATH,
)
from media_stack.services.apps.homepage.service import HomepageService
from media_stack.services.apps.homepage.adapters import (
    DEFAULT_HOSTS,
    render_services_yaml,
)


ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = ROOT / "contracts" / "auth.yaml"


class TestAuthContractPathResolution(unittest.TestCase):
    """Auth contract must be findable in both repo and container layouts."""

    def test_repo_path_exists(self) -> None:
        """In the repo, the contract file must exist at the repo-relative path."""
        self.assertTrue(CONTRACT_PATH.exists(),
            f"Auth contract not found at {CONTRACT_PATH}")

    def test_resolved_path_points_to_existing_file(self) -> None:
        """The resolved _CONTRACT_PATH must point to an existing file."""
        self.assertTrue(_CONTRACT_PATH.exists(),
            f"Resolved _CONTRACT_PATH {_CONTRACT_PATH} does not exist; "
            f"_resolve_contract_path() walked the candidate list "
            f"(repo / /opt/media-stack / /contracts) and none matched.",
        )

    def test_contract_loads_from_resolved_path(self) -> None:
        """AuthContractService must load successfully from the resolved path."""
        svc = AuthContractService(_CONTRACT_PATH)
        modes = svc.get_modes()
        self.assertIn("none", modes)
        self.assertIn("authelia", modes)

    def test_container_fallback_path_in_resolver_candidates(self) -> None:
        """The /contracts/auth.yaml container-standard path must be in
        the resolver's candidate list (so a stripped image without the
        repo or /opt/media-stack tree still finds the contract)."""
        from media_stack.domain.auth import gateway_policy as gp
        import inspect
        source = inspect.getsource(gp._resolve_contract_path)
        self.assertIn("/contracts/auth.yaml", source,
            "Container-fallback path /contracts/auth.yaml must appear "
            "in _resolve_contract_path()'s candidate list.")

    def test_contract_service_handles_missing_file_gracefully(self) -> None:
        """AuthContractService must not crash on missing contract file."""
        svc = AuthContractService(Path("/nonexistent/auth.yaml"))
        modes = svc.get_modes()
        self.assertEqual(modes, {})

    def test_api_auth_modes_returns_data(self) -> None:
        """The auth modes API (used by dashboard dropdown) must return modes."""
        from media_stack.api.services.auth_config import AuthConfigService
        svc = AuthConfigService()
        modes = svc.get_auth_modes()
        self.assertTrue(len(modes) >= 4,
            f"Expected at least 4 auth modes, got {len(modes)}: {modes}")
        keys = [m["key"] for m in modes]
        self.assertIn("none", keys)
        self.assertIn("authelia", keys)

    def test_api_oidc_providers_returns_data(self) -> None:
        """The OIDC providers API (used by dashboard dropdown) must return providers."""
        from media_stack.api.services.auth_config import AuthConfigService
        svc = AuthConfigService()
        providers = svc.get_oidc_providers()
        self.assertTrue(len(providers) >= 7,
            f"Expected at least 7 OIDC providers, got {len(providers)}")
        keys = [p["key"] for p in providers]
        self.assertIn("google", keys)
        self.assertIn("local", keys)


class TestHomepageServicesGeneration(unittest.TestCase):
    """Homepage must generate proper service tiles by default."""

    def _make_service(self) -> HomepageService:
        return HomepageService(
            bool_cfg=lambda cfg, key, default: cfg.get(key, default),
            coerce_list=lambda v: list(v) if isinstance(v, (list, tuple)) else [],
            resolve_path=lambda root, rel: Path(root) / rel,
            log=lambda msg: None,
            default_hosts=list(DEFAULT_HOSTS),
            render_services_yaml=render_services_yaml,
        )

    def test_default_enabled_generates_tiles(self) -> None:
        """Homepage service must generate tiles even without explicit config."""
        svc = self._make_service()
        with tempfile.TemporaryDirectory() as tmpdir:
            # Empty config — simulates a profile with no homepage section
            cfg: dict[str, Any] = {}
            changed = svc.ensure_services_config(cfg, tmpdir)
            self.assertTrue(changed, "Should generate services.yaml on first run")
            services_path = Path(tmpdir) / "homepage" / "services.yaml"
            self.assertTrue(services_path.exists(),
                "services.yaml must be created")
            content = services_path.read_text()
            self.assertIn("Media Stack:", content)
            self.assertIn("Jellyfin", content)
            self.assertIn("Sonarr", content)

    def test_gateway_urls_used_when_routing_configured(self) -> None:
        """Tiles must use gateway path-prefix URLs, not direct host URLs."""
        svc = self._make_service()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg: dict[str, Any] = {
                "routing": {
                    "gateway_host": "apps.media-stack.local",
                    "gateway_port": "80",
                    "app_path_prefix": "/app",
                },
            }
            svc.ensure_services_config(cfg, tmpdir)
            content = (Path(tmpdir) / "homepage" / "services.yaml").read_text()
            # Tiles should use gateway URLs
            self.assertIn("apps.media-stack.local/app/jellyfin", content,
                "Jellyfin tile must use gateway URL")
            self.assertIn("apps.media-stack.local/app/sonarr", content,
                "Sonarr tile must use gateway URL")
            # Should NOT contain direct host URLs
            self.assertNotIn("sonarr.local", content,
                "Tiles must not use direct host URLs when gateway is configured")

    def test_gateway_urls_include_port_when_non_standard(self) -> None:
        """Gateway URLs must include port when not 80 or 443."""
        svc = self._make_service()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg: dict[str, Any] = {
                "routing": {
                    "gateway_host": "apps.media-stack.local",
                    "gateway_port": "8880",
                    "app_path_prefix": "/app",
                },
            }
            svc.ensure_services_config(cfg, tmpdir)
            content = (Path(tmpdir) / "homepage" / "services.yaml").read_text()
            self.assertIn("apps.media-stack.local:8880/app/jellyfin", content)

    def test_explicit_disabled_skips_generation(self) -> None:
        """Homepage explicitly disabled should not generate tiles."""
        svc = self._make_service()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg: dict[str, Any] = {"homepage": {"enabled": False}}
            changed = svc.ensure_services_config(cfg, tmpdir)
            self.assertFalse(changed)

    def test_generated_tiles_include_all_default_services(self) -> None:
        """Generated tiles must include all standard services."""
        svc = self._make_service()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg: dict[str, Any] = {}
            svc.ensure_services_config(cfg, tmpdir)
            content = (Path(tmpdir) / "homepage" / "services.yaml").read_text()
            # Check key services are present
            for expected in ["Jellyfin", "Sonarr", "Radarr", "Prowlarr",
                           "qBittorrent", "Jellyseerr", "Homepage"]:
                self.assertIn(expected, content,
                    f"Missing tile for {expected}")

    def test_no_placeholder_content(self) -> None:
        """Generated tiles must NOT contain the stock Homepage placeholder."""
        svc = self._make_service()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg: dict[str, Any] = {}
            svc.ensure_services_config(cfg, tmpdir)
            content = (Path(tmpdir) / "homepage" / "services.yaml").read_text()
            self.assertNotIn("My First Service", content,
                "Generated config must not contain placeholder content")
            self.assertNotIn("My Second Service", content)

    def test_idempotent_no_change_on_rerun(self) -> None:
        """Running ensure_services_config twice should not change on second run."""
        svc = self._make_service()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg: dict[str, Any] = {}
            svc.ensure_services_config(cfg, tmpdir)
            changed = svc.ensure_services_config(cfg, tmpdir)
            self.assertFalse(changed, "Second run should be idempotent")

    def test_custom_hosts_override_defaults(self) -> None:
        """Custom hosts list replaces default hosts."""
        svc = self._make_service()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg: dict[str, Any] = {
                "homepage": {
                    "hosts": ["jellyfin.local", "sonarr.local"],
                },
            }
            svc.ensure_services_config(cfg, tmpdir)
            content = (Path(tmpdir) / "homepage" / "services.yaml").read_text()
            self.assertIn("Jellyfin", content)
            self.assertIn("Sonarr", content)
            # Other services should NOT be present
            self.assertNotIn("qBittorrent", content)


class TestHomepageDefaultHosts(unittest.TestCase):
    """DEFAULT_HOSTS must cover all standard services."""

    def test_includes_all_key_services(self) -> None:
        hosts_str = " ".join(DEFAULT_HOSTS)
        for svc in ["jellyfin", "sonarr", "radarr", "prowlarr",
                     "qbittorrent", "jellyseerr", "homepage",
                     "media-stack-controller"]:
            self.assertIn(svc, hosts_str,
                f"DEFAULT_HOSTS missing {svc}")

    def test_render_services_yaml_produces_valid_yaml(self) -> None:
        """Rendered services.yaml must be valid YAML."""
        content = render_services_yaml(DEFAULT_HOSTS)
        parsed = yaml.safe_load(content)
        self.assertIsInstance(parsed, list)
        self.assertTrue(len(parsed) > 0)
        # First group should be "Media Stack"
        first_group = parsed[0]
        self.assertIn("Media Stack", first_group)


class TestHomepageBootstrapFlagWiring(unittest.TestCase):
    """Verify the bootstrap flag name matches between operation plan and config resolver.

    Root cause of homepage tiles never generating: the operation plan referenced
    'configure_dashboard_services' but the config resolver sets 'configure_dashboard'.
    """

    def test_homepage_operation_plan_flag_matches_config_resolver(self) -> None:
        """The homepage handler's enabled_attr must match a real feature flag.

        Root cause regression: operation plan had 'configure_dashboard_services'
        but config resolver only sets 'configure_dashboard'. The handler never ran.
        """
        import json
        plans_path = (ROOT / "src" / "media_stack" / "contracts"
                      / "runner_operation_plans.json")
        plans = json.loads(plans_path.read_text())

        # Find the homepage handler's enabled_attr
        homepage_attr = None
        for section_name, section in plans.items():
            if not isinstance(section, dict):
                continue
            for step in section.get("steps", []):
                if step.get("handler") == "ensure_homepage_services_config":
                    homepage_attr = step.get("enabled_attr", "")
                    break

        self.assertIsNotNone(homepage_attr,
            "ensure_homepage_services_config must exist in operation plans")
        self.assertTrue(homepage_attr,
            "ensure_homepage_services_config must have an enabled_attr")

        # The flag must exist in the config resolver's output
        from media_stack.services.apps.integrations.config_resolver import (
            resolve_integration_configs,
        )
        result = resolve_integration_configs({})
        self.assertIn(homepage_attr, result.feature_flags,
            f"Operation plan references '{homepage_attr}' for homepage handler "
            f"but config resolver does not set this flag. "
            f"Available: {sorted(result.feature_flags.keys())}")
        # And it must be True by default
        self.assertTrue(result.feature_flags[homepage_attr],
            f"'{homepage_attr}' must be True by default")

    def test_homepage_enabled_by_default(self) -> None:
        """HomepageConfig must default to enabled=True when no config is provided."""
        from media_stack.services.apps.integrations.config_models import HomepageConfig
        cfg = HomepageConfig.from_dict(None)
        self.assertTrue(cfg.enabled,
            "HomepageConfig must default to enabled=True")

    def test_homepage_enabled_from_empty_dict(self) -> None:
        """HomepageConfig must default to enabled=True from empty dict."""
        from media_stack.services.apps.integrations.config_models import HomepageConfig
        cfg = HomepageConfig.from_dict({})
        self.assertTrue(cfg.enabled,
            "HomepageConfig must default to enabled=True from empty dict")

    def test_homepage_can_be_explicitly_disabled(self) -> None:
        from media_stack.services.apps.integrations.config_models import HomepageConfig
        cfg = HomepageConfig.from_dict({"enabled": False})
        self.assertFalse(cfg.enabled)

    def test_configure_dashboard_flag_true_by_default(self) -> None:
        """With no homepage section, configure_dashboard must be True."""
        from media_stack.services.apps.integrations.config_resolver import (
            resolve_integration_configs,
        )
        result = resolve_integration_configs({})
        self.assertTrue(result.feature_flags.get("configure_dashboard"),
            "configure_dashboard must be True by default so Homepage tiles are generated")


if __name__ == "__main__":
    unittest.main()
