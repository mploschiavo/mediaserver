"""Scripted deploy pipeline step helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

InfoFn = Callable[[str], None]
RunScriptFn = Callable[..., None]


@dataclass(frozen=True)
class DeployPipelineConfig:
    namespace: str
    root_dir: Path
    prepare_host_root: str
    enable_components: str
    selected_apps: str
    internet_exposed: str
    route_strategy: str
    ingress_domain: str
    app_gateway_host: str
    app_gateway_port: str
    app_path_prefix: str
    media_server_direct_host: str
    preconfigure_api_keys: str
    apply_initial_preferences: str
    auto_download_content: str
    config_file: Path
    auth_provider: str = ""
    auth_middleware: str = ""
    edge_router_provider: str = ""


@dataclass
class DeployPipelineService:
    cfg: DeployPipelineConfig
    info: InfoFn
    run_script: RunScriptFn

    def prepare_host_directories(self, storage_mode: str) -> bool:
        self.info(
            "Skipping host directory prep (dynamic PVC mode only; "
            f"requested storage mode: {storage_mode})."
        )
        return False

    def generate_secrets(self) -> None:
        self.info("Generating secure secrets in cluster before bootstrap")
        self.run_script(
            "generate-secrets.sh",
            env={
                "NAMESPACE": self.cfg.namespace,
                "OUTPUT_FILE": str(self.cfg.root_dir / "secrets.generated.env"),
            },
        )

    def apply_scale_policy_guardrails(self) -> None:
        self.info("Applying scale-policy guardrails")
        self.run_script(
            "apply-scale-policy.sh",
            str(self.cfg.config_file),
            env={"NAMESPACE": self.cfg.namespace},
        )

    def run_bootstrap_pipeline(self) -> None:
        self.info("Running full bootstrap pipeline")
        env = {
            "NAMESPACE": self.cfg.namespace,
            "PREPARE_HOST_ROOT": self.cfg.prepare_host_root,
            "ENABLE_COMPONENTS": self.cfg.enable_components,
            "SELECTED_APPS": self.cfg.selected_apps,
            "INTERNET_EXPOSED": self.cfg.internet_exposed,
            "ROUTE_STRATEGY": self.cfg.route_strategy,
            "INGRESS_DOMAIN": self.cfg.ingress_domain,
            "APP_GATEWAY_HOST": self.cfg.app_gateway_host,
            "APP_PATH_PREFIX": self.cfg.app_path_prefix,
            "MEDIA_SERVER_DIRECT_HOST": self.cfg.media_server_direct_host,
            "AUTH_PROVIDER": self.cfg.auth_provider,
            "AUTH_MIDDLEWARE": self.cfg.auth_middleware,
            "EDGE_ROUTER_PROVIDER": self.cfg.edge_router_provider,
            "PRECONFIGURE_API_KEYS": self.cfg.preconfigure_api_keys,
            "APPLY_INITIAL_PREFERENCES": self.cfg.apply_initial_preferences,
            "FULLY_PRECONFIGURED": self.cfg.apply_initial_preferences,
            "AUTO_DOWNLOAD_CONTENT": self.cfg.auto_download_content,
        }
        gateway_port = str(self.cfg.app_gateway_port or "").strip()
        if gateway_port:
            env["APP_GATEWAY_PORT"] = gateway_port
            env["TRAEFIK_HTTP_PORT"] = gateway_port
        self.run_script(
            "bootstrap-all.sh",
            str(self.cfg.config_file),
            env=env,
        )
