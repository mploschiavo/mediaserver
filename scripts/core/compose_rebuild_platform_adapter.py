"""Docker Compose-backed implementation for rebuild platform adapter."""

from __future__ import annotations

import os
import re
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from core.docker import DockerClient, DockerContainerState
from core.platform_adapter import InfoFn, PlatformEnvironmentRef

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _parse_wait_seconds(value: str, *, default_seconds: int = 300) -> int:
    token = str(value or "").strip().lower()
    if not token:
        return default_seconds
    if token.endswith("ms"):
        token = token[:-2]
        try:
            return max(1, int(float(token) / 1000.0))
        except Exception:
            return default_seconds
    unit = token[-1:] if token else ""
    raw = token[:-1] if unit in {"s", "m", "h"} else token
    try:
        magnitude = float(raw)
    except Exception:
        return default_seconds
    multiplier = 1.0
    if unit == "m":
        multiplier = 60.0
    elif unit == "h":
        multiplier = 3600.0
    return max(1, int(magnitude * multiplier))


def _parse_duration_nanoseconds(value: str, *, default_ns: int) -> int:
    token = str(value or "").strip().lower()
    if not token:
        return default_ns
    if token.endswith("ms"):
        try:
            return int(float(token[:-2]) * 1_000_000)
        except Exception:
            return default_ns
    unit = token[-1:] if token else ""
    raw = token[:-1] if unit in {"s", "m", "h"} else token
    try:
        magnitude = float(raw)
    except Exception:
        return default_ns
    multiplier = 1.0
    if unit == "m":
        multiplier = 60.0
    elif unit == "h":
        multiplier = 3600.0
    return int(magnitude * multiplier * 1_000_000_000)


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
    target: str = "compose"


@dataclass
class ComposeRebuildPlatformAdapter:
    cfg: ComposeRebuildPlatformConfig
    info: InfoFn
    docker: DockerClient
    environment: PlatformEnvironmentRef = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "environment",
            PlatformEnvironmentRef(
                environment_id=self.cfg.environment_id,
                target=self.cfg.target,
            ),
        )

    def _project_name(self) -> str:
        project = str(self.cfg.compose_project_name or "").strip()
        return project or self.cfg.environment_id

    def _selected_app_set(self) -> set[str]:
        return {str(item).strip().lower() for item in self.cfg.selected_apps if str(item).strip()}

    def _read_env_file(self) -> dict[str, str]:
        env_path = self.cfg.compose_env_file
        if env_path is None or not env_path.exists():
            return {}
        values: dict[str, str] = {}
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, raw_value = line.partition("=")
            values[key.strip()] = raw_value.strip()
        return values

    def _compose_env(self) -> dict[str, str]:
        out = dict(os.environ)
        out.update(self._read_env_file())
        return out

    def _expand_string(self, value: str, env: dict[str, str]) -> str:
        def _replace(match: re.Match[str]) -> str:
            key = match.group(1).strip()
            return str(env.get(key, ""))

        return _ENV_PATTERN.sub(_replace, value)

    def _expand_value(self, value: Any, env: dict[str, str]) -> Any:
        if isinstance(value, str):
            return self._expand_string(value, env)
        if isinstance(value, list):
            return [self._expand_value(item, env) for item in value]
        if isinstance(value, dict):
            return {str(key): self._expand_value(item, env) for key, item in value.items()}
        return value

    def _load_compose_spec(self) -> dict[str, Any]:
        if not self.cfg.compose_file.exists():
            raise RuntimeError(f"Compose file not found: {self.cfg.compose_file}")
        payload = yaml.safe_load(self.cfg.compose_file.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError(f"Compose file is invalid: {self.cfg.compose_file}")
        expanded = self._expand_value(payload, self._compose_env())
        if not isinstance(expanded, dict):
            raise RuntimeError(f"Compose file expansion failed: {self.cfg.compose_file}")
        return expanded

    def _selected_services(self, services: dict[str, Any]) -> dict[str, dict[str, Any]]:
        selected_apps = self._selected_app_set()
        selected_profiles = {item for item in self.cfg.compose_profiles if item}
        profile_filtered: dict[str, dict[str, Any]] = {}
        for service_name, raw_spec in services.items():
            if not isinstance(raw_spec, dict):
                continue
            profiles = raw_spec.get("profiles")
            service_key = str(service_name).strip().lower()
            selected_by_app = bool(selected_apps and service_key in selected_apps)
            if not profiles:
                profile_filtered[str(service_name)] = dict(raw_spec)
                continue
            profile_values = {str(item).strip() for item in profiles if str(item).strip()}
            if selected_profiles.intersection(profile_values) or selected_by_app:
                profile_filtered[str(service_name)] = dict(raw_spec)

        if not selected_apps:
            return profile_filtered

        keep: set[str] = {
            str(item).strip() for item in self.cfg.edge_router_service_names if str(item).strip()
        }
        keep.update(selected_apps)

        def _dependencies(spec: dict[str, Any]) -> tuple[str, ...]:
            raw_depends = spec.get("depends_on")
            if isinstance(raw_depends, list):
                return tuple(str(item).strip() for item in raw_depends if str(item).strip())
            if isinstance(raw_depends, dict):
                return tuple(str(key).strip() for key in raw_depends.keys() if str(key).strip())
            return ()

        expanded = True
        while expanded:
            expanded = False
            for service_name, spec in profile_filtered.items():
                if service_name not in keep:
                    continue
                for dependency in _dependencies(spec):
                    if dependency in profile_filtered and dependency not in keep:
                        keep.add(dependency)
                        expanded = True

        out: dict[str, dict[str, Any]] = {}
        for service_name, spec in profile_filtered.items():
            if service_name in keep:
                out[service_name] = dict(spec)
        return out

    def _service_order(self, services: dict[str, dict[str, Any]]) -> list[str]:
        order: list[str] = []
        visited: set[str] = set()
        visiting: set[str] = set()

        def _dependencies(spec: dict[str, Any]) -> tuple[str, ...]:
            raw_depends = spec.get("depends_on")
            if isinstance(raw_depends, list):
                return tuple(str(item).strip() for item in raw_depends if str(item).strip())
            if isinstance(raw_depends, dict):
                return tuple(str(key).strip() for key in raw_depends.keys() if str(key).strip())
            return ()

        def _visit(name: str) -> None:
            if name in visited:
                return
            if name in visiting:
                return
            visiting.add(name)
            spec = services.get(name) or {}
            for dependency in _dependencies(spec):
                if dependency in services:
                    _visit(dependency)
            visiting.remove(name)
            visited.add(name)
            order.append(name)

        for service_name in services.keys():
            _visit(service_name)
        return order

    def _container_name(self, service_name: str, spec: dict[str, Any]) -> str:
        explicit = str(spec.get("container_name") or "").strip()
        if explicit:
            return explicit
        return f"{self._project_name()}_{service_name}_1"

    def _normalize_environment(self, spec: dict[str, Any]) -> dict[str, str]:
        raw_env = spec.get("environment")
        env: dict[str, str] = {}
        if isinstance(raw_env, dict):
            for key, value in raw_env.items():
                env[str(key)] = str(value)
        elif isinstance(raw_env, list):
            for item in raw_env:
                token = str(item or "").strip()
                if "=" not in token:
                    continue
                key, _, value = token.partition("=")
                env[key.strip()] = value
        return env

    def _normalize_labels(self, service_name: str, spec: dict[str, Any]) -> dict[str, str]:
        labels: dict[str, str] = {}
        raw_labels = spec.get("labels")
        if isinstance(raw_labels, dict):
            for key, value in raw_labels.items():
                labels[str(key)] = str(value)
        elif isinstance(raw_labels, list):
            for item in raw_labels:
                token = str(item or "").strip()
                if "=" not in token:
                    continue
                key, _, value = token.partition("=")
                labels[key.strip()] = value
        labels.setdefault("com.docker.compose.project", self._project_name())
        labels.setdefault("com.docker.compose.service", service_name)
        self._apply_edge_routing_labels(service_name, labels)
        self._apply_auth_labels(service_name, labels)
        return labels

    def _edge_router_provider(self) -> str:
        return str(self.cfg.edge_router_provider or "").strip().lower()

    def _edge_provider_spec(self) -> dict[str, str]:
        provider = self._edge_router_provider()
        if not provider:
            return {}
        specs = self.cfg.edge_compose_provider_specs or {}
        raw_spec = specs.get(provider) if isinstance(specs, dict) else None
        if not isinstance(raw_spec, dict):
            return {}
        out: dict[str, str] = {}
        for raw_key, raw_value in raw_spec.items():
            key = str(raw_key or "").strip()
            value = str(raw_value or "").strip()
            if key and value:
                out[key] = value
        return out

    @staticmethod
    def _format_template(template: str, **kwargs: object) -> str:
        try:
            return str(template).format(**kwargs)
        except Exception:
            return ""

    def _router_names(self, labels: dict[str, str]) -> set[str]:
        prefix = str(self._edge_provider_spec().get("router_label_prefix") or "").strip()
        if not prefix:
            return set()
        names: set[str] = set()
        for key in labels.keys():
            if not key.startswith(prefix):
                continue
            suffix = key[len(prefix) :]
            if "." not in suffix:
                continue
            router_name = suffix.split(".", 1)[0].strip()
            if router_name:
                names.add(router_name)
        return names

    def _apply_router_middleware(
        self, labels: dict[str, str], router_name: str, middleware_name: str
    ) -> None:
        if not middleware_name:
            return
        key_template = str(
            self._edge_provider_spec().get("router_middleware_key_template") or ""
        ).strip()
        if not key_template:
            return
        key = self._format_template(key_template, router_name=router_name)
        if not key:
            return
        existing = str(labels.get(key, "") or "").strip()
        items = [item.strip() for item in existing.split(",") if item.strip()]
        if middleware_name not in items:
            items.append(middleware_name)
        labels[key] = ",".join(items)

    def _is_edge_router_service(self, labels: dict[str, str]) -> bool:
        enable_key = str(self._edge_provider_spec().get("enable_label_key") or "").strip()
        if not enable_key:
            return False
        enabled = str(labels.get(enable_key, "") or "").strip().lower()
        return enabled in {"true", "1", "yes", "on"}

    def _route_strategy(self) -> str:
        strategy = str(self.cfg.route_strategy or "").strip().lower()
        allowed = tuple(
            str(item or "").strip().lower()
            for item in tuple(self.cfg.allowed_route_strategies or ())
            if str(item or "").strip()
        )
        if strategy and strategy in set(allowed):
            return strategy
        return allowed[0] if allowed else strategy

    def _path_route_prefix(self, service_name: str) -> str:
        token = str(self.cfg.app_path_prefix or "").strip()
        if not token:
            token = "/app"
        if not token.startswith("/"):
            token = f"/{token}"
        token = token.rstrip("/")
        return f"{token}/{service_name}"

    def _clear_router_labels(self, labels: dict[str, str]) -> None:
        prefix = str(self._edge_provider_spec().get("router_label_prefix") or "").strip()
        if not prefix:
            return
        for key in list(labels.keys()):
            if key.startswith(prefix):
                labels.pop(key, None)

    def _is_media_server_service(self, service_name: str) -> bool:
        service_key = str(service_name or "").strip().lower()
        media_services = {
            str(item or "").strip().lower()
            for item in tuple(self.cfg.media_server_service_names or ())
            if str(item or "").strip()
        }
        return bool(service_key and service_key in media_services)

    def _apply_edge_routing_labels(self, service_name: str, labels: dict[str, str]) -> None:
        if not self._is_edge_router_service(labels):
            return
        spec = self._edge_provider_spec()
        strategy = self._route_strategy()
        is_media_server = self._is_media_server_service(service_name)
        gateway_host = str(self.cfg.app_gateway_host or "").strip()

        if strategy == "path-prefix" and gateway_host and not is_media_server:
            self._clear_router_labels(labels)

        if strategy in {"path-prefix", "hybrid"} and gateway_host:
            path_router = f"{service_name}-path"
            path_prefix = self._path_route_prefix(service_name)
            strip_name = f"{service_name}-stripprefix"
            router_rule_key_template = str(spec.get("router_rule_key_template") or "").strip()
            router_service_key_template = str(spec.get("router_service_key_template") or "").strip()
            strip_prefix_key_template = str(spec.get("strip_prefix_key_template") or "").strip()
            path_rule_template = str(spec.get("path_rule_template") or "").strip()

            rule_key = self._format_template(router_rule_key_template, router_name=path_router)
            if rule_key and path_rule_template:
                labels[rule_key] = self._format_template(
                    path_rule_template,
                    gateway_host=gateway_host,
                    path_prefix=path_prefix,
                    service_name=service_name,
                    router_name=path_router,
                )

            service_key = self._format_template(
                router_service_key_template,
                router_name=path_router,
            )
            if service_key:
                labels[service_key] = service_name

            strip_key = self._format_template(
                strip_prefix_key_template,
                middleware_name=strip_name,
                service_name=service_name,
            )
            if strip_key:
                labels[strip_key] = path_prefix

            self._apply_router_middleware(labels, path_router, strip_name)

        if is_media_server:
            direct_host = str(self.cfg.media_server_direct_host or "").strip()
            if direct_host:
                media_rule_key_template = str(
                    spec.get("media_server_rule_key_template") or ""
                ).strip()
                media_rule_template = str(spec.get("direct_host_rule_template") or "").strip()
                media_rule_key = self._format_template(
                    media_rule_key_template,
                    service_name=service_name,
                )
                if media_rule_key and media_rule_template:
                    labels[media_rule_key] = self._format_template(
                        media_rule_template,
                        direct_host=direct_host,
                        service_name=service_name,
                    )

    def _auth_middleware(self) -> str:
        explicit = str(self.cfg.auth_middleware or "").strip()
        if explicit:
            return explicit
        provider = str(self.cfg.auth_provider or "").strip().lower()
        defaults = {
            str(key or "").strip().lower(): str(value or "").strip()
            for key, value in dict(self.cfg.auth_provider_middleware_defaults or {}).items()
            if str(key or "").strip()
        }
        return str(defaults.get(provider) or "").strip()

    def _apply_auth_labels(self, service_name: str, labels: dict[str, str]) -> None:
        if not bool(self.cfg.internet_exposed):
            return
        middleware = self._auth_middleware()
        if not middleware:
            return
        if self._is_media_server_service(service_name):
            # Media server keeps direct-app connectivity with native auth for TV/mobile clients.
            return
        for router_name in sorted(self._router_names(labels)):
            self._apply_router_middleware(labels, router_name, middleware)

    def _normalize_ports(self, spec: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for raw in spec.get("ports") or []:
            token = str(raw or "").strip().strip('"').strip("'")
            if not token:
                continue
            protocol = "tcp"
            if "/" in token:
                token, protocol = token.rsplit("/", 1)
                protocol = protocol or "tcp"
            segments = token.split(":")
            if len(segments) == 1:
                container_port = segments[0]
                out[f"{container_port}/{protocol}"] = None
                continue
            host_ip = ""
            host_port = ""
            container_port = ""
            if len(segments) == 2:
                host_port, container_port = segments
            else:
                host_ip, host_port, container_port = segments[-3], segments[-2], segments[-1]
            if not container_port:
                continue
            key = f"{container_port}/{protocol}"
            try:
                port_value = int(host_port)
            except Exception:
                continue
            if host_ip:
                out[key] = (host_ip, port_value)
            else:
                out[key] = port_value
        return out

    def _normalize_volumes(self, spec: dict[str, Any]) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        for raw in spec.get("volumes") or []:
            token = str(raw or "").strip().strip('"').strip("'")
            if not token:
                continue
            segments = token.split(":")
            if len(segments) < 2:
                continue
            host = segments[0]
            container = segments[1]
            mode = segments[2] if len(segments) > 2 else "rw"
            host_path = Path(host)
            if not host_path.is_absolute():
                host_path = (self.cfg.compose_file.parent / host_path).resolve()
            out[str(host_path)] = {"bind": container, "mode": mode}
        return out

    def _normalize_healthcheck(self, spec: dict[str, Any]) -> dict[str, Any] | None:
        raw = spec.get("healthcheck")
        if not isinstance(raw, dict):
            return None
        out: dict[str, Any] = {}
        test = raw.get("test")
        if isinstance(test, list):
            out["test"] = [str(item) for item in test]
        elif isinstance(test, str):
            out["test"] = ["CMD-SHELL", test]
        if "interval" in raw:
            out["interval"] = _parse_duration_nanoseconds(
                str(raw.get("interval")), default_ns=30_000_000_000
            )
        if "timeout" in raw:
            out["timeout"] = _parse_duration_nanoseconds(
                str(raw.get("timeout")), default_ns=10_000_000_000
            )
        if "start_period" in raw:
            out["start_period"] = _parse_duration_nanoseconds(
                str(raw.get("start_period")),
                default_ns=0,
            )
        if "retries" in raw:
            try:
                out["retries"] = int(raw.get("retries"))
            except Exception:
                pass
        return out or None

    def _create_or_replace_service_container(
        self,
        service_name: str,
        spec: dict[str, Any],
        *,
        default_network: str,
    ) -> None:
        image = str(spec.get("image") or "").strip()
        if not image:
            raise RuntimeError(f"Compose service '{service_name}' is missing an image.")
        container_name = self._container_name(service_name, spec)
        self.docker.pull_image(image)
        self.docker.remove_container(container_name, force=True)

        restart_name = str(spec.get("restart") or "").strip()
        restart_policy = {"Name": restart_name} if restart_name else None
        network_mode = str(spec.get("network_mode") or "").strip()
        command_value = spec.get("command")
        command: str | list[str] | None = None
        if isinstance(command_value, list):
            command = [str(item) for item in command_value]
        elif command_value is not None:
            command = str(command_value)

        kwargs: dict[str, Any] = {
            "image": image,
            "name": container_name,
            "detach": True,
            "labels": self._normalize_labels(service_name, spec),
            "environment": self._normalize_environment(spec),
            "volumes": self._normalize_volumes(spec),
            "ports": self._normalize_ports(spec),
            "devices": [str(item) for item in (spec.get("devices") or []) if str(item).strip()],
            "network_mode": network_mode or None,
            "user": str(spec.get("user") or "") or None,
            "group_add": [str(item) for item in (spec.get("group_add") or []) if str(item).strip()],
            "healthcheck": self._normalize_healthcheck(spec),
            "command": command,
        }
        if restart_policy is not None:
            kwargs["restart_policy"] = restart_policy
        if not network_mode:
            kwargs["network"] = default_network

        container = self.docker.create_container(**kwargs)
        try:
            container.start()
        except Exception as exc:
            raise RuntimeError(f"Failed starting compose service '{service_name}': {exc}") from exc

    def _target_states(
        self, services: dict[str, dict[str, Any]]
    ) -> dict[str, DockerContainerState | None]:
        out: dict[str, DockerContainerState | None] = {}
        for service_name, spec in services.items():
            out[service_name] = self.docker.container_state(
                self._container_name(service_name, spec)
            )
        return out

    def delete_environment_optional(self, delete_environment: str) -> bool:
        if delete_environment != "1":
            return False

        self.docker.ping()
        compose = self._load_compose_spec()
        services = self._selected_services(dict(compose.get("services") or {}))
        removed = 0
        for service_name, spec in services.items():
            if self.docker.remove_container(self._container_name(service_name, spec), force=True):
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
        services = self._selected_services(dict(compose.get("services") or {}))
        if not services:
            raise RuntimeError("No compose services selected for deployment.")
        default_network = f"{self._project_name()}_default"
        self.docker.ensure_network(default_network)
        order = self._service_order(services)
        self.info(
            f"Compose target: deploying {len(order)} service(s) for project '{self._project_name()}'."
        )
        self.info(
            "Compose edge config: "
            f"route_strategy={self._route_strategy()}, "
            f"internet_exposed={self.cfg.internet_exposed}, "
            f"auth_provider={self.cfg.auth_provider or '<unset>'}"
        )
        if self.cfg.app_gateway_host:
            self.info(f"Compose edge gateway host: {self.cfg.app_gateway_host}")
        if self.cfg.media_server_direct_host:
            self.info(f"Compose media-server direct host: {self.cfg.media_server_direct_host}")
        for service_name in order:
            self._create_or_replace_service_container(
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
        deadline = time.time() + _parse_wait_seconds(self.cfg.wait_timeout, default_seconds=300)
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
