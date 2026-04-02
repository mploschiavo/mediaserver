"""Compose container normalization and deployment helpers."""

from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.platforms.compose.docker_client import DockerClient, DockerContainerState
from core.platforms.compose.services.labels import ComposeLabelService
from core.platforms.compose.services.spec import ComposeSpecResolver, parse_duration_nanoseconds


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
            except Exception:
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
