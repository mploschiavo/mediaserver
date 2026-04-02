"""Docker Compose-backed implementation for rebuild platform adapter."""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from core.platform_adapter import InfoFn, PlatformEnvironmentRef
from core.platforms.compose.docker_client import DockerClient
from core.platforms.compose.services.container_runtime import ComposeContainerRuntimeService
from core.platforms.compose.services.labels import ComposeLabelConfig, ComposeLabelService
from core.platforms.compose.services.runtime_artifacts import ComposeRuntimeArtifactService
from core.platforms.compose.services.spec import ComposeSpecResolver, parse_wait_seconds
from core.platforms.compose.services.traefik_dynamic_config import TraefikDynamicConfigService


@dataclass(frozen=True)
class ComposeRebuildPlatformConfig:
    environment_id: str
    compose_file: Path
    compose_env_file: Path | None = None
    compose_project_name: str = ""
    compose_profiles: tuple[str, ...] = ()
    selected_apps: tuple[str, ...] = ()
    internet_exposed: bool = False
    route_strategy: str = "subdomain"
    allowed_route_strategies: tuple[str, ...] = ()
    app_gateway_host: str = ""
    app_path_prefix: str = "/app"
    media_server_direct_host: str = ""
    auth_provider: str = ""
    auth_middleware: str = ""
    edge_router_provider: str = ""
    edge_router_service_names: tuple[str, ...] = ()
    edge_compose_provider_specs: dict[str, dict[str, str]] = field(default_factory=dict)
    auth_provider_middleware_defaults: dict[str, str] = field(default_factory=dict)
    media_server_service_names: tuple[str, ...] = ()
    wait_timeout: str = "20m"
    node_ip: str = ""
    runtime_artifacts_dir: Path | None = None
    target: str = "compose"


@dataclass
class ComposeRebuildPlatformAdapter:
    cfg: ComposeRebuildPlatformConfig
    info: InfoFn
    docker: DockerClient
    environment: PlatformEnvironmentRef = field(init=False)
    spec_resolver: ComposeSpecResolver = field(init=False, repr=False)
    label_service: ComposeLabelService = field(init=False, repr=False)
    runtime_service: ComposeContainerRuntimeService = field(init=False, repr=False)
    artifacts_service: ComposeRuntimeArtifactService = field(init=False, repr=False)
    traefik_dynamic_config_service: TraefikDynamicConfigService = field(
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "environment",
            PlatformEnvironmentRef(
                environment_id=self.cfg.environment_id,
                target=self.cfg.target,
            ),
        )
        self.spec_resolver = ComposeSpecResolver(
            compose_file=self.cfg.compose_file,
            compose_env_file=self.cfg.compose_env_file,
            compose_project_name=self.cfg.compose_project_name,
            environment_id=self.cfg.environment_id,
            compose_profiles=tuple(self.cfg.compose_profiles or ()),
            selected_apps=tuple(self.cfg.selected_apps or ()),
            edge_router_service_names=tuple(self.cfg.edge_router_service_names or ()),
        )
        self.label_service = ComposeLabelService(
            cfg=ComposeLabelConfig(
                project_name=self._project_name(),
                route_strategy=self.cfg.route_strategy,
                allowed_route_strategies=tuple(self.cfg.allowed_route_strategies or ()),
                app_gateway_host=self.cfg.app_gateway_host,
                app_path_prefix=self.cfg.app_path_prefix,
                media_server_direct_host=self.cfg.media_server_direct_host,
                internet_exposed=bool(self.cfg.internet_exposed),
                auth_provider=self.cfg.auth_provider,
                auth_middleware=self.cfg.auth_middleware,
                edge_router_provider=self.cfg.edge_router_provider,
                edge_compose_provider_specs=dict(self.cfg.edge_compose_provider_specs or {}),
                auth_provider_middleware_defaults=dict(
                    self.cfg.auth_provider_middleware_defaults or {}
                ),
                media_server_service_names=tuple(self.cfg.media_server_service_names or ()),
            )
        )
        self.runtime_service = ComposeContainerRuntimeService(
            compose_file=self.cfg.compose_file,
            docker=self.docker,
            spec_resolver=self.spec_resolver,
            label_service=self.label_service,
        )
        self.artifacts_service = ComposeRuntimeArtifactService(
            runtime_artifacts_dir=self.cfg.runtime_artifacts_dir,
            info=self.info,
        )
        self.traefik_dynamic_config_service = TraefikDynamicConfigService(
            label_service=self.label_service,
            spec_resolver=self.spec_resolver,
        )

    def _project_name(self) -> str:
        return self.spec_resolver.project_name()

    def _load_compose_spec(self) -> dict[str, Any]:
        return self.spec_resolver.load_compose_spec()

    def _selected_services(self, services: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return self.spec_resolver.selected_services(services)

    def _service_order(self, services: dict[str, dict[str, Any]]) -> list[str]:
        return self.spec_resolver.service_order(services)

    def _target_states(self, services: dict[str, dict[str, Any]]):
        return self.runtime_service.target_states(services)

    def _runtime_selected_compose_payload(
        self,
        *,
        compose_spec: dict[str, Any],
        services: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "services": {},
            "name": self._project_name(),
        }
        for key in ("networks", "volumes"):
            value = compose_spec.get(key)
            if isinstance(value, dict) and value:
                payload[key] = dict(value)
        services_payload: dict[str, dict[str, Any]] = {}
        for service_name, spec in services.items():
            service_payload = dict(spec)
            service_payload["labels"] = self.label_service.normalize_labels(service_name, spec)
            service_payload.setdefault(
                "container_name", self.spec_resolver.container_name(service_name, spec)
            )
            services_payload[service_name] = service_payload
        payload["services"] = services_payload
        return payload

    def _write_traefik_dynamic_config(
        self,
        services: dict[str, dict[str, Any]],
    ) -> None:
        if self.label_service.edge_router_provider() != "traefik":
            return

        config_root = self.spec_resolver.config_root()
        if config_root is None:
            self.info(
                "Compose edge routing: CONFIG_ROOT/COMPOSE_CONFIG_ROOT not set; "
                "skipping Traefik file-provider runtime config."
            )
            return

        rendered = self.traefik_dynamic_config_service.render(services)
        dynamic_file = config_root / "traefik" / "dynamic" / "media-stack.dynamic.yaml"
        dynamic_file.parent.mkdir(parents=True, exist_ok=True)
        dynamic_file.write_text(
            yaml.safe_dump(rendered.payload, sort_keys=False),
            encoding="utf-8",
        )
        self.info(
            "Compose Traefik dynamic config: "
            f"{dynamic_file} "
            f"(routers={rendered.router_count}, services={rendered.service_count}, "
            f"middlewares={rendered.middleware_count})."
        )
        self.artifacts_service.write_yaml_artifact(
            "resolved/traefik.dynamic.runtime.yaml",
            rendered.payload,
            label="Compose Traefik dynamic config artifact",
        )

    def delete_environment_optional(self, delete_environment: str) -> bool:
        if delete_environment != "1":
            return False

        self.docker.ping()
        compose = self._load_compose_spec()
        services = self._selected_services(dict(compose.get("services") or {}))
        removed = 0
        for service_name, spec in services.items():
            if self.docker.remove_container(
                self.spec_resolver.container_name(service_name, spec), force=True
            ):
                removed += 1
        for container in self.docker.list_project_containers(self._project_name()):
            name = str(getattr(container, "name", "") or "")
            if not name:
                continue
            if self.docker.remove_container(name, force=True):
                removed += 1
        self.docker.remove_network(f"{self._project_name()}_default")
        self.info(
            f"Compose target: removed {removed} container(s) for project '{self._project_name()}'."
        )
        return True

    def apply_environment_definition(self) -> None:
        self.docker.ping()
        compose = self._load_compose_spec()
        self.artifacts_service.write_yaml_artifact(
            "resolved/docker-compose.expanded.yaml",
            compose,
            label="Compose expanded runtime spec artifact",
        )
        services = self._selected_services(dict(compose.get("services") or {}))
        if not services:
            raise RuntimeError("No compose services selected for deployment.")
        self._write_traefik_dynamic_config(services)
        self.artifacts_service.write_yaml_artifact(
            "resolved/docker-compose.selected.runtime.yaml",
            self._runtime_selected_compose_payload(compose_spec=compose, services=services),
            label="Compose selected runtime spec artifact",
        )
        default_network = f"{self._project_name()}_default"
        self.docker.ensure_network(default_network)
        order = self._service_order(services)
        self.artifacts_service.write_json_artifact(
            "resolved/deploy-plan.json",
            {
                "created_at": int(time.time()),
                "project_name": self._project_name(),
                "selected_services": order,
                "route_strategy": self.label_service.route_strategy(),
                "internet_exposed": bool(self.cfg.internet_exposed),
                "auth_provider": str(self.cfg.auth_provider or "").strip().lower(),
                "edge_router_provider": self.label_service.edge_router_provider(),
                "app_gateway_host": str(self.cfg.app_gateway_host or "").strip(),
                "media_server_direct_host": str(self.cfg.media_server_direct_host or "").strip(),
            },
            label="Compose deploy plan artifact",
        )
        self.info(
            f"Compose target: deploying {len(order)} service(s) for project '{self._project_name()}'."
        )
        self.info(
            "Compose edge config: "
            f"route_strategy={self.label_service.route_strategy()}, "
            f"internet_exposed={self.cfg.internet_exposed}, "
            f"auth_provider={self.cfg.auth_provider or '<unset>'}"
        )
        if self.cfg.app_gateway_host:
            self.info(f"Compose edge gateway host: {self.cfg.app_gateway_host}")
        if self.cfg.media_server_direct_host:
            self.info(f"Compose media-server direct host: {self.cfg.media_server_direct_host}")
        for service_name in order:
            self.runtime_service.create_or_replace_service_container(
                service_name,
                services[service_name],
                default_network=default_network,
            )

    def reconcile_edge_routing(self) -> bool:
        # Compose networking/labels are applied as part of container creation.
        return False

    def wait_for_workloads(self) -> None:
        compose = self._load_compose_spec()
        services = self._selected_services(dict(compose.get("services") or {}))
        if not services:
            raise RuntimeError("No compose services selected for readiness checks.")
        deadline = time.time() + parse_wait_seconds(self.cfg.wait_timeout, default_seconds=300)
        while time.time() <= deadline:
            unresolved: list[str] = []
            states = self._target_states(services)
            for service_name, state in states.items():
                if state is None:
                    unresolved.append(f"{service_name}:missing")
                    continue
                if state.status != "running":
                    unresolved.append(f"{service_name}:{state.status or 'unknown'}")
                    continue
                has_healthcheck = isinstance(
                    (services.get(service_name) or {}).get("healthcheck"), dict
                )
                if has_healthcheck and state.health and state.health != "healthy":
                    unresolved.append(f"{service_name}:health={state.health}")
            if not unresolved:
                return
            time.sleep(2)
        raise RuntimeError(
            "Compose workload readiness timed out for project "
            f"'{self._project_name()}' (timeout={self.cfg.wait_timeout})."
        )

    def run_smoke_test(self) -> str:
        compose = self._load_compose_spec()
        services = self._selected_services(dict(compose.get("services") or {}))
        states = self._target_states(services)
        running = sum(1 for state in states.values() if state and state.status == "running")
        self.info(
            f"Compose smoke check: {running}/{len(states)} selected service containers are running."
        )
        node_ip = str(self.cfg.node_ip or "").strip()
        if node_ip:
            return node_ip
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(("8.8.8.8", 80))
            ip = str(sock.getsockname()[0] or "").strip()
            sock.close()
        except Exception:
            ip = ""
        if not ip:
            ip = "127.0.0.1"
        return ip

    def print_workload_status(self) -> None:
        compose = self._load_compose_spec()
        services = self._selected_services(dict(compose.get("services") or {}))
        for service_name, state in self._target_states(services).items():
            if state is None:
                self.info(f"compose/{service_name}: missing")
                continue
            health = state.health or "<none>"
            self.info(
                f"compose/{service_name}: status={state.status} health={health} image={state.image}"
            )

    def backup_secret_values(self, preserve_secret_on_rebuild: str) -> dict[str, str]:
        return {}

    def restore_secret_values(self, values: dict[str, str]) -> None:
        return None
