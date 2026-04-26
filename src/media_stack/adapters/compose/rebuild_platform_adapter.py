"""Docker Compose-backed implementation for rebuild platform adapter."""

from __future__ import annotations

import re
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from media_stack.core.platform_adapter import InfoFn, PlatformEnvironmentRef
from media_stack.infrastructure.platforms.compose.docker_client import DockerClient
from media_stack.adapters.compose.edge.provider_contract import (
    ComposeEdgeProviderRuntimeContext,
    ComposeEdgeRuntimePatchFn,
)
from media_stack.adapters.compose.edge.provider_registry import build_compose_edge_runtime_patchers
from media_stack.adapters.compose.services.container_runtime import ComposeContainerRuntimeService
from media_stack.adapters.compose.services.edge_http_smoke import ComposeEdgeHttpSmokeService
from media_stack.adapters.compose.services.edge_route_graph import ComposeEdgeRouteGraphService
from media_stack.adapters.compose.services.labels import ComposeLabelConfig, ComposeLabelService
from media_stack.adapters.compose.services.runtime_artifacts import ComposeRuntimeArtifactService
from media_stack.adapters.compose.services.spec import ComposeSpecResolver, parse_wait_seconds


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
    app_gateway_port: str = ""
    app_path_prefix: str = "/app"
    media_server_direct_host: str = ""
    auth_provider: str = ""
    auth_middleware: str = ""
    edge_router_provider: str = ""
    edge_router_service_names: tuple[str, ...] = ()
    edge_path_prefix_redirect_service_names: tuple[str, ...] = ()
    edge_path_prefix_preserve_service_names: tuple[str, ...] = ()
    edge_compose_provider_specs: dict[str, dict[str, str]] = field(default_factory=dict)
    auth_provider_middleware_defaults: dict[str, str] = field(default_factory=dict)
    media_server_service_names: tuple[str, ...] = ()
    wait_timeout: str = "20m"
    node_ip: str = ""
    disk_allocation_gb: int = 500
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
    edge_route_graph_service: ComposeEdgeRouteGraphService = field(init=False, repr=False)
    edge_http_smoke_service: ComposeEdgeHttpSmokeService = field(init=False, repr=False)
    edge_runtime_patchers: dict[str, ComposeEdgeRuntimePatchFn] = field(init=False, repr=False)

    @staticmethod
    def _service_env_token(service_name: str) -> str:
        return re.sub(r"[^A-Za-z0-9]+", "_", str(service_name or "").strip()).strip("_").upper()

    def _path_prefix_service_env_overrides(self) -> dict[str, str]:
        strategy = str(self.cfg.route_strategy or "").strip().lower()
        use_prefix_paths = strategy in {"path-prefix", "hybrid"}

        app_prefix = str(self.cfg.app_path_prefix or "").strip() or "/app"
        if not app_prefix.startswith("/"):
            app_prefix = f"/{app_prefix}"
        app_prefix = app_prefix.rstrip("/") or "/app"

        try:
            raw_payload = yaml.safe_load(self.cfg.compose_file.read_text(encoding="utf-8"))
        except Exception:
            raw_payload = {}
        services = dict((raw_payload or {}).get("services") or {})

        overrides: dict[str, str] = {}
        for raw_service_name in services.keys():
            service_name = str(raw_service_name or "").strip()
            if not service_name:
                continue
            token = self._service_env_token(service_name)
            if not token:
                continue
            key = f"{token}_BASE_PATH"
            if use_prefix_paths:
                overrides[key] = f"{app_prefix}/{service_name}".replace("//", "/")
            else:
                overrides[key] = ""
        return overrides

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "environment",
            PlatformEnvironmentRef(
                environment_id=self.cfg.environment_id,
                target=self.cfg.target,
            ),
        )
        environment_overrides = {
            "APP_GATEWAY_HOST": str(self.cfg.app_gateway_host or "").strip(),
            "APP_PATH_PREFIX": str(self.cfg.app_path_prefix or "").strip(),
            "MEDIA_SERVER_DIRECT_HOST": str(self.cfg.media_server_direct_host or "").strip(),
            **self._path_prefix_service_env_overrides(),
        }
        app_gateway_port = str(self.cfg.app_gateway_port or "").strip()
        if app_gateway_port:
            environment_overrides["APP_GATEWAY_PORT"] = app_gateway_port
            environment_overrides["EDGE_HTTP_PORT"] = app_gateway_port
            environment_overrides["TRAEFIK_HTTP_PORT"] = app_gateway_port

        self.spec_resolver = ComposeSpecResolver(
            compose_file=self.cfg.compose_file,
            compose_env_file=self.cfg.compose_env_file,
            compose_project_name=self.cfg.compose_project_name,
            environment_id=self.cfg.environment_id,
            compose_profiles=tuple(self.cfg.compose_profiles or ()),
            selected_apps=tuple(self.cfg.selected_apps or ()),
            edge_router_service_names=tuple(self.cfg.edge_router_service_names or ()),
            environment_overrides=environment_overrides,
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
                path_prefix_redirect_service_names=tuple(
                    self.cfg.edge_path_prefix_redirect_service_names or ()
                ),
                path_prefix_preserve_service_names=tuple(
                    self.cfg.edge_path_prefix_preserve_service_names or ()
                ),
            )
        )
        self.runtime_service = ComposeContainerRuntimeService(
            compose_file=self.cfg.compose_file,
            docker=self.docker,
            spec_resolver=self.spec_resolver,
            label_service=self.label_service,
            info=self.info,
        )
        self.artifacts_service = ComposeRuntimeArtifactService(
            runtime_artifacts_dir=self.cfg.runtime_artifacts_dir,
            info=self.info,
        )
        self.edge_route_graph_service = ComposeEdgeRouteGraphService(
            label_service=self.label_service,
            spec_resolver=self.spec_resolver,
        )
        self.edge_http_smoke_service = ComposeEdgeHttpSmokeService(
            label_service=self.label_service,
            spec_resolver=self.spec_resolver,
            route_graph_service=self.edge_route_graph_service,
            info=self.info,
        )
        self.edge_runtime_patchers = build_compose_edge_runtime_patchers(
            ComposeEdgeProviderRuntimeContext(
                label_service=self.label_service,
                spec_resolver=self.spec_resolver,
                route_graph_service=self.edge_route_graph_service,
                artifacts_service=self.artifacts_service,
                info=self.info,
            )
        )

    def _project_name(self) -> str:
        return self.spec_resolver.project_name()

    def _load_compose_spec(self) -> dict[str, Any]:
        return self.spec_resolver.load_compose_spec()

    def _validate_compose_environment_contract(self) -> None:
        compose_text = self.cfg.compose_file.read_text(encoding="utf-8", errors="replace")
        compose_env = self.spec_resolver.compose_environment()
        required_keys: list[str] = []
        for key in ("CONFIG_ROOT", "MEDIA_ROOT", "DATA_ROOT"):
            if f"${{{key}}}" in compose_text or f"${{COMPOSE_{key}}}" in compose_text:
                required_keys.append(key)
        missing = [key for key in required_keys if not str(compose_env.get(key) or "").strip()]
        if not missing:
            return
        env_file_hint = (
            str(self.cfg.compose_env_file)
            if self.cfg.compose_env_file is not None
            else "<unset compose env file>"
        )
        example_hint = str(
            self.cfg.compose_file.parent / ".env.example"
            if self.cfg.compose_env_file is not None
            else "<see docker/.env.example>"
        )
        raise RuntimeError(
            "Compose environment is missing required variables: "
            + ", ".join(missing)
            + f". Create {env_file_hint} (see {example_hint} for reference) "
            "and set the missing paths, then rerun deploy."
        )

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

    def _write_edge_runtime_config(
        self,
        services: dict[str, dict[str, Any]],
    ) -> None:
        provider = self.label_service.edge_router_provider()
        patcher = self.edge_runtime_patchers.get(provider)
        result = patcher(services) if patcher is not None else None
        if (
            result is not None
            and not result.applied
            and provider
            and provider != "none"
            and not self.label_service.edge_provider_has_compose_label_spec()
        ):
            self.info(
                "Compose edge provider "
                f"'{provider}' is active with stub/no-op compose label bindings; "
                "routing labels and dynamic edge patch generation are skipped."
            )
        if result is None and provider and provider != "none":
            self.info(
                "Compose edge provider "
                f"'{provider}' has no registered runtime patch plugin; "
                "edge runtime patch generation is skipped."
            )

    def delete_environment_optional(self, delete_environment: str) -> bool:
        if delete_environment != "1":
            return False

        self.info(
            f"[DESTRUCTIVE] Tearing down compose environment for project '{self._project_name()}' "
            "(DELETE_NAMESPACE=1). All selected containers and the project network will be removed."
        )
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
        self._validate_compose_environment_contract()
        compose = self._load_compose_spec()
        self.artifacts_service.write_yaml_artifact(
            "resolved/docker-compose.expanded.yaml",
            compose,
            label="Compose expanded runtime spec artifact",
        )
        services = self._selected_services(dict(compose.get("services") or {}))
        if not services:
            raise RuntimeError("No compose services selected for deployment.")
        storage_report = self.runtime_service.enforce_storage_budget(
            services,
            disk_allocation_gb=int(self.cfg.disk_allocation_gb),
        )
        self.artifacts_service.write_json_artifact(
            "resolved/storage-budget.report.json",
            storage_report,
            label="Compose storage budget report artifact",
        )
        self.runtime_service.assert_host_ports_available(services)
        self._write_edge_runtime_config(services)
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
                "app_gateway_port": str(self.cfg.app_gateway_port or "").strip(),
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
        if self.cfg.app_gateway_port:
            self.info(f"Compose edge gateway port: {self.cfg.app_gateway_port}")
        if self.cfg.media_server_direct_host:
            self.info(f"Compose media-server direct host: {self.cfg.media_server_direct_host}")
        for service_name in order:
            self.runtime_service.create_or_replace_service_container(
                service_name,
                services[service_name],
                default_network=default_network,
            )

    def _chaos_target_services(self, services: dict[str, dict[str, Any]]) -> tuple[str, ...]:
        router_service_names = {
            str(item or "").strip()
            for item in tuple(self.cfg.edge_router_service_names or ())
            if str(item or "").strip()
        }
        candidates: list[str] = []
        for service_name in self._service_order(services):
            if service_name in router_service_names:
                continue
            candidates.append(service_name)
        if not candidates:
            candidates = list(self._service_order(services))
        return tuple(candidates)

    def _run_chaos_action(
        self,
        *,
        action: str,
        service_name: str,
        service_spec: dict[str, Any],
        default_network: str,
    ) -> None:
        container_name = self.spec_resolver.container_name(service_name, service_spec)
        container = self.docker.get_container(container_name)
        if container is None:
            raise RuntimeError(
                "Compose chaos action could not find target container "
                f"'{container_name}' for service '{service_name}'."
            )
        action_token = str(action or "").strip().lower()
        if action_token == "restart_container":
            container.restart(timeout=10)
            return
        if action_token == "pause_container":
            container.pause()
            time.sleep(5)
            container.unpause()
            return
        if action_token == "network_disconnect":
            network_mode = str(service_spec.get("network_mode") or "").strip().lower()
            if network_mode == "host":
                self.info(
                    "Compose chaos network_disconnect skipped for host-network service "
                    f"'{service_name}'."
                )
                return
            network = self.docker.client.networks.get(default_network)
            network.disconnect(container, force=True)
            time.sleep(3)
            network.connect(container)
            return
        raise RuntimeError(f"Unsupported compose chaos action '{action}'.")

    def run_chaos_tests(
        self,
        *,
        duration_minutes: int,
        interval_seconds: int,
        actions: tuple[str, ...],
    ) -> None:
        compose = self._load_compose_spec()
        services = self._selected_services(dict(compose.get("services") or {}))
        if not services:
            raise RuntimeError("Compose chaos tests require at least one selected service.")
        resolved_actions = tuple(
            str(item or "").strip().lower() for item in actions if str(item or "").strip()
        )
        if not resolved_actions:
            raise RuntimeError("Compose chaos tests require at least one action.")
        targets = self._chaos_target_services(services)
        if not targets:
            raise RuntimeError("Compose chaos tests could not resolve eligible target services.")

        window_seconds = max(60, int(duration_minutes) * 60)
        start = time.time()
        deadline = start + window_seconds
        spacing = max(0, int(interval_seconds))
        default_network = f"{self._project_name()}_default"

        self.info(
            "Compose chaos schedule: "
            f"duration_minutes={duration_minutes}, interval_seconds={spacing}, "
            f"actions={','.join(resolved_actions)}, targets={','.join(targets)}"
        )
        for idx, action in enumerate(resolved_actions):
            scheduled_at = start + (idx * spacing)
            if scheduled_at > deadline:
                self.info(
                    "Compose chaos schedule window reached before running action "
                    f"'{action}'; stopping early."
                )
                break
            now = time.time()
            if scheduled_at > now:
                time.sleep(scheduled_at - now)
            service_name = targets[idx % len(targets)]
            self.info(
                f"Compose chaos action starting: action={action}, service={service_name}, "
                f"sequence={idx + 1}/{len(resolved_actions)}"
            )
            self._run_chaos_action(
                action=action,
                service_name=service_name,
                service_spec=services[service_name],
                default_network=default_network,
            )
            self.wait_for_workloads()
            self.info(f"Compose chaos action healed: action={action}, service={service_name}")

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
        self.edge_http_smoke_service.run(services)
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
