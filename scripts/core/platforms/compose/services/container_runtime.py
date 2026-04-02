"""Compose container normalization and deployment helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.platforms.compose.docker_client import DockerClient, DockerContainerState
from core.platforms.compose.services.labels import ComposeLabelService
from core.platforms.compose.services.spec import ComposeSpecResolver, parse_duration_nanoseconds


@dataclass
class ComposeContainerRuntimeService:
    compose_file: Path
    docker: DockerClient
    spec_resolver: ComposeSpecResolver
    label_service: ComposeLabelService

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
                host_path = (self.compose_file.parent / host_path).resolve()
            out[str(host_path)] = {"bind": container, "mode": mode}
        return out

    @staticmethod
    def _normalize_healthcheck(spec: dict[str, Any]) -> dict[str, Any] | None:
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
            out["interval"] = parse_duration_nanoseconds(
                str(raw.get("interval")), default_ns=30_000_000_000
            )
        if "timeout" in raw:
            out["timeout"] = parse_duration_nanoseconds(
                str(raw.get("timeout")), default_ns=10_000_000_000
            )
        if "start_period" in raw:
            out["start_period"] = parse_duration_nanoseconds(
                str(raw.get("start_period")),
                default_ns=0,
            )
        if "retries" in raw:
            try:
                out["retries"] = int(raw.get("retries"))
            except Exception:
                pass
        return out or None

    def create_or_replace_service_container(
        self,
        service_name: str,
        spec: dict[str, Any],
        *,
        default_network: str,
    ) -> None:
        image = str(spec.get("image") or "").strip()
        if not image:
            raise RuntimeError(f"Compose service '{service_name}' is missing an image.")
        container_name = self.spec_resolver.container_name(service_name, spec)
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
            "labels": self.label_service.normalize_labels(service_name, spec),
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

    def target_states(
        self, services: dict[str, dict[str, Any]]
    ) -> dict[str, DockerContainerState | None]:
        out: dict[str, DockerContainerState | None] = {}
        for service_name, spec in services.items():
            out[service_name] = self.docker.container_state(
                self.spec_resolver.container_name(service_name, spec)
            )
        return out
