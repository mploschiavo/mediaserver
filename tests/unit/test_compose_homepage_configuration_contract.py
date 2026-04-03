import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_lib.homepage import DEFAULT_HOSTS, render_services_yaml  # noqa: E402
from bootstrap_services.apps.homepage.service import HomepageService  # noqa: E402
from bootstrap_services.apps.stack.bootstrap_config_policy import (  # noqa: E402
    apply_bootstrap_runtime_policy,
)


class ComposeHomepageConfigurationContractTests(unittest.TestCase):
    def test_compose_homepage_uses_generated_services_yaml(self):
        compose_path = ROOT / "docker" / "docker-compose.yml"
        payload = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
        services = payload.get("services") or {}
        homepage = services.get("homepage") or {}
        volumes = [str(item).strip() for item in (homepage.get("volumes") or [])]

        self.assertIn("${CONFIG_ROOT}/homepage:/app/config", volumes)
        self.assertFalse(
            any("/app/config/services.yaml" in entry for entry in volumes),
            "Homepage must use generated config from ${CONFIG_ROOT}/homepage/services.yaml "
            "and must not mount a static services.yaml override.",
        )

    def test_compose_homepage_allowed_hosts_includes_traefik_port_variant(self):
        compose_path = ROOT / "docker" / "docker-compose.yml"
        payload = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
        services = payload.get("services") or {}
        homepage = services.get("homepage") or {}
        env_cfg = homepage.get("environment") or {}
        allowed_hosts = str(env_cfg.get("HOMEPAGE_ALLOWED_HOSTS") or "")
        self.assertIn("${HOMEPAGE_HOST}:${TRAEFIK_HTTP_PORT}", allowed_hosts)
        self.assertIn("${APP_GATEWAY_HOST}:${TRAEFIK_HTTP_PORT}", allowed_hosts)

    def test_bootstrap_homepage_config_includes_homepage_and_jellyfin_entries(self):
        cfg = json.loads((ROOT / "bootstrap" / "media-stack.bootstrap.json").read_text("utf-8"))
        service = HomepageService(
            bool_cfg=lambda obj, key, default=False: bool((obj or {}).get(key, default)),
            coerce_list=lambda value: (
                value if isinstance(value, list) else ([] if value is None else [value])
            ),
            resolve_path=lambda base, rel: Path(base) / rel,
            log=lambda _msg: None,
            default_hosts=list(DEFAULT_HOSTS),
            render_services_yaml=render_services_yaml,
        )

        with tempfile.TemporaryDirectory() as tmp:
            changed = service.ensure_services_config(cfg, tmp)
            self.assertTrue(changed)
            rendered = (Path(tmp) / "homepage" / "services.yaml").read_text("utf-8")

        self.assertIn("Homepage:", rendered)
        self.assertIn("Jellyfin:", rendered)
        self.assertIn("http://homepage.local", rendered)
        self.assertIn("http://jellyfin.local", rendered)
        self.assertIn("Device Onboarding:", rendered)

    def test_compose_hybrid_runtime_policy_rewrites_homepage_links(self):
        cfg = json.loads((ROOT / "bootstrap" / "media-stack.bootstrap.json").read_text("utf-8"))
        apply_bootstrap_runtime_policy(
            cfg,
            selected_apps_csv=(
                "jellyfin,jellyseerr,sonarr,radarr,bazarr,prowlarr,"
                "qbittorrent,sabnzbd,maintainerr,unpackerr,homepage,flaresolverr"
            ),
            auto_download_content=False,
            internet_exposed=False,
            route_strategy="hybrid",
            ingress_domain="media-dev.local",
            app_gateway_host="apps.media-dev.local",
            app_path_prefix="/app",
            media_server_direct_host="jellyfin.media-dev.local",
        )
        service = HomepageService(
            bool_cfg=lambda obj, key, default=False: bool((obj or {}).get(key, default)),
            coerce_list=lambda value: (
                value if isinstance(value, list) else ([] if value is None else [value])
            ),
            resolve_path=lambda base, rel: Path(base) / rel,
            log=lambda _msg: None,
            default_hosts=list(DEFAULT_HOSTS),
            render_services_yaml=render_services_yaml,
        )

        with tempfile.TemporaryDirectory() as tmp:
            service.ensure_services_config(cfg, tmp)
            rendered = (Path(tmp) / "homepage" / "services.yaml").read_text("utf-8")

        self.assertIn("http://homepage.local", rendered)
        self.assertIn("http://jellyfin.media-dev.local", rendered)
        self.assertIn("http://apps.media-dev.local/app/jellyseerr", rendered)

    def test_compose_hybrid_runtime_policy_rewrites_homepage_links_with_gateway_port(self):
        cfg = json.loads((ROOT / "bootstrap" / "media-stack.bootstrap.json").read_text("utf-8"))
        apply_bootstrap_runtime_policy(
            cfg,
            selected_apps_csv=(
                "jellyfin,jellyseerr,sonarr,radarr,bazarr,prowlarr,"
                "qbittorrent,sabnzbd,maintainerr,unpackerr,homepage,flaresolverr"
            ),
            auto_download_content=False,
            internet_exposed=False,
            route_strategy="hybrid",
            ingress_domain="media-dev.local",
            app_gateway_host="apps.media-dev.local",
            app_gateway_port="18080",
            app_path_prefix="/app",
            media_server_direct_host="jellyfin.media-dev.local",
        )
        service = HomepageService(
            bool_cfg=lambda obj, key, default=False: bool((obj or {}).get(key, default)),
            coerce_list=lambda value: (
                value if isinstance(value, list) else ([] if value is None else [value])
            ),
            resolve_path=lambda base, rel: Path(base) / rel,
            log=lambda _msg: None,
            default_hosts=list(DEFAULT_HOSTS),
            render_services_yaml=render_services_yaml,
        )

        with tempfile.TemporaryDirectory() as tmp:
            service.ensure_services_config(cfg, tmp)
            rendered = (Path(tmp) / "homepage" / "services.yaml").read_text("utf-8")

        self.assertIn("http://homepage.local:18080", rendered)
        self.assertIn("http://jellyfin.media-dev.local:18080", rendered)
        self.assertIn("http://apps.media-dev.local:18080/app/jellyseerr", rendered)


if __name__ == "__main__":
    unittest.main()
