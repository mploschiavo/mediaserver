"""Compose container normalization and deployment helpers."""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from shutil import disk_usage
from typing import Any

from media_stack.core.platforms.compose.docker_client import DockerClient, DockerContainerState
from media_stack.core.platforms.compose.services.labels import ComposeLabelService
from media_stack.core.platforms.compose.services.spec import ComposeSpecResolver, parse_duration_nanoseconds
import logging


@dataclass(frozen=True)
class ComposeVolumeBind:
    host_path: Path
    container_path: str
    mode: str = "rw"


@dataclass
class ComposeContainerRuntimeService:
    compose_file: Path
    docker: DockerClient
    spec_resolver: ComposeSpecResolver
    label_service: ComposeLabelService
    info: Callable[[str], None] | None = None

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
        # Keep Unpackerr running before bootstrap key stitching by replacing
        # legacy non-32-char placeholders with a syntactically valid sentinel.
        placeholder = "replace-after-first-boot"
        placeholder32 = "00000000000000000000000000000000"
        for key, value in list(env.items()):
            env_key = str(key or "").strip().upper()
            env_value = str(value or "").strip()
            if not env_key.startswith("UN_") or not env_key.endswith("_API_KEY"):
                continue
            if env_value.lower() == placeholder:
                env[key] = placeholder32
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
            except Exception as exc:
                log_swallowed(exc)
                continue
            if host_ip:
                out[key] = (host_ip, port_value)
            else:
                out[key] = port_value
        return out

    def _volume_binds(self, spec: dict[str, Any]) -> list[ComposeVolumeBind]:
        out: list[ComposeVolumeBind] = []
        for raw in tuple(spec.get("volumes") or ()):
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
            out.append(
                ComposeVolumeBind(
                    host_path=host_path,
                    container_path=str(container),
                    mode=str(mode),
                )
            )
        return out

    @staticmethod
    def _is_read_only_mode(mode: str) -> bool:
        tokens = {
            str(item or "").strip().lower()
            for item in str(mode or "rw").split(",")
            if str(item or "").strip()
        }
        return "ro" in tokens

    @staticmethod
    def _parse_user_ids(spec: dict[str, Any]) -> tuple[int, int | None] | None:
        raw = str(spec.get("user") or "").strip()
        if not raw:
            return None
        if ":" not in raw:
            if raw.isdigit():
                return int(raw), None
            return None
        uid_raw, _, gid_raw = raw.partition(":")
        if not uid_raw.isdigit():
            return None
        uid = int(uid_raw)
        gid = int(gid_raw) if gid_raw.isdigit() else None
        return uid, gid

    @staticmethod
    def _path_writable_for_user(path: Path, *, uid: int, gid: int | None) -> bool:
        if uid == 0:
            return True
        st = path.stat()
        mode = st.st_mode
        need_exec = path.is_dir()

        def _check(write_bit: int, exec_bit: int) -> bool:
            writable = bool(mode & write_bit)
            if not writable:
                return False
            if not need_exec:
                return True
            return bool(mode & exec_bit)

        if uid == st.st_uid and _check(stat.S_IWUSR, stat.S_IXUSR):
            return True
        if gid is not None and gid == st.st_gid and _check(stat.S_IWGRP, stat.S_IXGRP):
            return True
        if _check(stat.S_IWOTH, stat.S_IXOTH):
            return True
        return False

    def _normalize_volumes(
        self, volume_binds: list[ComposeVolumeBind]
    ) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        for bind in volume_binds:
            out[str(bind.host_path)] = {
                "bind": str(bind.container_path),
                "mode": str(bind.mode),
            }
        return out

    def _preflight_bind_mount_paths(
        self,
        service_name: str,
        *,
        volume_binds: list[ComposeVolumeBind],
        user_ids: tuple[int, int | None] | None,
    ) -> None:
        for bind in volume_binds:
            host_path = bind.host_path
            read_only = self._is_read_only_mode(bind.mode)
            if not host_path.exists():
                if read_only:
                    raise RuntimeError(
                        f"Compose service '{service_name}' bind mount path is missing: "
                        f"{host_path} (mode={bind.mode})."
                    )
                host_path.mkdir(parents=True, exist_ok=True)

            if read_only or user_ids is None:
                continue
            uid, gid = user_ids
            if self._path_writable_for_user(host_path, uid=uid, gid=gid):
                continue
            st = host_path.stat()
            raise RuntimeError(
                f"Compose service '{service_name}' bind mount path is not writable for "
                f"user '{uid}:{gid if gid is not None else ''}': {host_path} "
                f"(owner={st.st_uid}:{st.st_gid}, mode={oct(st.st_mode & 0o777)}). "
                "Use target-specific compose bind roots or fix mount ownership/permissions."
            )

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
            except Exception as exc:
                log_swallowed(exc)
        return out or None

    @staticmethod
    def _normalize_host_ip(value: Any) -> str:
        token = str(value or "").strip()
        if not token or token in {"0.0.0.0", "::"}:
            return "0.0.0.0"
        return token

    @classmethod
    def _host_ip_conflicts(cls, requested: str, occupied: str) -> bool:
        req = cls._normalize_host_ip(requested)
        occ = cls._normalize_host_ip(occupied)
        return req == "0.0.0.0" or occ == "0.0.0.0" or req == occ

    def _requested_host_ports(
        self, services: dict[str, dict[str, Any]]
    ) -> list[tuple[str, str, int, str]]:
        out: list[tuple[str, str, int, str]] = []
        for service_name, spec in services.items():
            for key, value in self._normalize_ports(spec).items():
                if value is None:
                    continue
                protocol = str(key).split("/", 1)[1] if "/" in str(key) else "tcp"
                if isinstance(value, tuple):
                    host_ip, host_port = value[0], value[1]
                else:
                    host_ip, host_port = "0.0.0.0", value
                try:
                    parsed_port = int(host_port)
                except Exception as exc:
                    log_swallowed(exc)
                    continue
                out.append((service_name, self._normalize_host_ip(host_ip), parsed_port, protocol))
        return out

    def _running_port_owners(
        self,
        *,
        ignored_container_names: set[str],
    ) -> dict[tuple[str, int, str], str]:
        owners: dict[tuple[str, int, str], str] = {}
        for container in self.docker.list_running_containers():
            container_name = str(getattr(container, "name", "") or "").strip()
            if not container_name or container_name in ignored_container_names:
                continue
            attrs = dict(getattr(container, "attrs", {}) or {})
            network_settings = dict(attrs.get("NetworkSettings") or {})
            ports = dict(network_settings.get("Ports") or {})
            for key, mappings in ports.items():
                protocol = str(key).split("/", 1)[1] if "/" in str(key) else "tcp"
                if not isinstance(mappings, list):
                    continue
                for item in mappings:
                    if not isinstance(item, dict):
                        continue
                    host_ip = self._normalize_host_ip(item.get("HostIp"))
                    host_port_raw = str(item.get("HostPort") or "").strip()
                    if not host_port_raw.isdigit():
                        continue
                    host_port = int(host_port_raw)
                    owners[(host_ip, host_port, protocol)] = container_name
        return owners

    def assert_host_ports_available(self, services: dict[str, dict[str, Any]]) -> None:
        requested = self._requested_host_ports(services)
        if not requested:
            return
        ignored_container_names = {
            self.spec_resolver.container_name(service_name, spec)
            for service_name, spec in services.items()
        }
        owners = self._running_port_owners(ignored_container_names=ignored_container_names)
        conflicts: list[str] = []
        for service_name, host_ip, host_port, protocol in requested:
            owner = ""
            for (owner_ip, owner_port, owner_protocol), owner_name in owners.items():
                if protocol != owner_protocol or host_port != owner_port:
                    continue
                if not self._host_ip_conflicts(host_ip, owner_ip):
                    continue
                owner = owner_name
                break
            if owner:
                conflicts.append(
                    f"{service_name}:{host_ip}:{host_port}/{protocol} (in use by {owner})"
                )
        if conflicts:
            raise RuntimeError(
                "Compose preflight detected host-port collisions. Resolve conflicts before deploy: "
                + ", ".join(conflicts)
            )
        if self.info:
            summary = ", ".join(
                f"{service}:{ip}:{port}/{protocol}" for service, ip, port, protocol in requested
            )
            self.info(f"Compose preflight: host port bindings are available ({summary}).")

    @staticmethod
    def _dedupe_storage_roots(paths: list[Path]) -> tuple[Path, ...]:
        roots: list[Path] = []
        for path in sorted((item.resolve() for item in paths), key=lambda item: len(item.parts)):
            if any(path == root or root in path.parents for root in roots):
                continue
            roots.append(path)
        return tuple(roots)

    @staticmethod
    def _path_usage_bytes(path: Path) -> int:
        if not path.exists():
            return 0
        if path.is_file():
            try:
                return int(path.stat().st_size)
            except Exception:
                return 0
        total = 0
        for base, _, files in os.walk(path):
            base_path = Path(base)
            for file_name in files:
                try:
                    total += int((base_path / file_name).stat().st_size)
                except Exception as exc:
                    log_swallowed(exc)
                    continue
        return total

    def enforce_storage_budget(
        self,
        services: dict[str, dict[str, Any]],
        *,
        disk_allocation_gb: int,
    ) -> dict[str, Any]:
        roots = self._dedupe_storage_roots(
            [bind.host_path for spec in services.values() for bind in self._volume_binds(spec)]
        )
        budget_bytes = int(max(1, int(disk_allocation_gb)) * 1024 * 1024 * 1024)
        root_reports: list[dict[str, Any]] = []
        used_bytes = 0
        for root in roots:
            resolved = root.resolve()
            item_used_bytes = self._path_usage_bytes(resolved)
            used_bytes += item_used_bytes
            try:
                fs_usage = disk_usage(resolved if resolved.exists() else resolved.parent)
                fs_total_bytes = int(fs_usage.total)
                fs_free_bytes = int(fs_usage.free)
            except Exception:
                fs_total_bytes = 0
                fs_free_bytes = 0
            root_reports.append(
                {
                    "path": str(resolved),
                    "exists": bool(resolved.exists()),
                    "used_bytes": int(item_used_bytes),
                    "filesystem_total_bytes": fs_total_bytes,
                    "filesystem_free_bytes": fs_free_bytes,
                }
            )
        over_budget = used_bytes > budget_bytes
        report = {
            "disk_allocation_gb": int(disk_allocation_gb),
            "budget_bytes": int(budget_bytes),
            "estimated_stack_used_bytes": int(used_bytes),
            "over_budget": bool(over_budget),
            "storage_roots": root_reports,
        }
        if self.info:
            used_gb = used_bytes / (1024 * 1024 * 1024)
            budget_gb = budget_bytes / (1024 * 1024 * 1024)
            self.info(
                "Compose storage guardrail: "
                f"estimated_used_gb={used_gb:.2f}, budget_gb={budget_gb:.2f}, "
                f"roots={len(root_reports)}"
            )
        if over_budget:
            raise RuntimeError(
                "Compose storage guardrail exceeded: estimated stack usage "
                f"{used_bytes} bytes exceeds configured budget {budget_bytes} bytes "
                f"(STACK_DISK_ALLOCATION_GB={disk_allocation_gb})."
            )
        return report

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
        volume_binds = self._volume_binds(spec)
        self._preflight_bind_mount_paths(
            service_name,
            volume_binds=volume_binds,
            user_ids=self._parse_user_ids(spec),
        )
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
            "volumes": self._normalize_volumes(volume_binds),
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
