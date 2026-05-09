"""Compose spec loading, selection, and planning helpers."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_ENV_PATTERN = re.compile(
    r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:(?P<op>:?[-+?])(?P<arg>[^}]*))?\}"
)


class ComposeDurationParser:
    """Compose-style duration token parser (e.g. ``30s``, ``5m``, ``100ms``)."""

    def parse_wait_seconds(self, value: str, *, default_seconds: int = 300) -> int:
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

    def parse_duration_nanoseconds(self, value: str, *, default_ns: int) -> int:
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


_DURATION_PARSER = ComposeDurationParser()
parse_wait_seconds = _DURATION_PARSER.parse_wait_seconds
parse_duration_nanoseconds = _DURATION_PARSER.parse_duration_nanoseconds


@dataclass(frozen=True)
class ComposeSpecResolver:
    compose_file: Path
    compose_env_file: Path | None = None
    compose_project_name: str = ""
    environment_id: str = ""
    compose_profiles: tuple[str, ...] = ()
    selected_apps: tuple[str, ...] = ()
    edge_router_service_names: tuple[str, ...] = ()
    environment_overrides: dict[str, str] = field(default_factory=dict)

    def project_name(self) -> str:
        project = str(self.compose_project_name or "").strip()
        return project or self.environment_id

    def selected_app_set(self) -> set[str]:
        return {str(item).strip().lower() for item in self.selected_apps if str(item or "").strip()}

    def _read_env_file(self) -> dict[str, str]:
        env_path = self.compose_env_file
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
        for raw_key, raw_value in dict(self.environment_overrides or {}).items():
            key = str(raw_key or "").strip()
            if not key:
                continue
            out[key] = str(raw_value or "").strip()
        return out

    def compose_environment(self) -> dict[str, str]:
        return self._compose_env()

    def config_root(self) -> Path | None:
        env = self._compose_env()
        token = str(env.get("COMPOSE_CONFIG_ROOT") or env.get("CONFIG_ROOT") or "").strip()
        if not token:
            return None
        return Path(token).expanduser()

    @staticmethod
    def _expand_string(value: str, env: dict[str, str]) -> str:
        def _replace(match: re.Match[str]) -> str:
            key = (match.group("name") or "").strip()
            op = match.group("op") or ""
            arg = match.group("arg") or ""
            present = key in env
            empty = present and env.get(key, "") == ""
            # Match the docker-compose interpolation rules — see
            # https://docs.docker.com/reference/compose-file/interpolation/
            #   ${VAR:-default}  use default if unset OR empty
            #   ${VAR-default}   use default if unset (empty kept)
            #   ${VAR:+alt}      use alt if set AND non-empty
            #   ${VAR+alt}       use alt if set (empty counts)
            #   ${VAR:?err}      treat unset/empty as error (we render empty)
            #   ${VAR?err}       treat unset as error (we render empty)
            if op == ":-":
                return arg if (not present or empty) else env[key]
            if op == "-":
                return arg if not present else env[key]
            if op == ":+":
                return arg if present and not empty else ""
            if op == "+":
                return arg if present else ""
            if op in (":?", "?"):
                # Don't crash the generator over an unset var; fall back
                # to the variable's value (empty when unset). The
                # missing-required-var case is the operator's bug to fix
                # in the .env file, not for the renderer to abort on.
                return env.get(key, "")
            return str(env.get(key, ""))

        return _ENV_PATTERN.sub(_replace, value)

    @classmethod
    def _expand_value(cls, value: Any, env: dict[str, str]) -> Any:
        if isinstance(value, str):
            return cls._expand_string(value, env)
        if isinstance(value, list):
            return [cls._expand_value(item, env) for item in value]
        if isinstance(value, dict):
            return {str(key): cls._expand_value(item, env) for key, item in value.items()}
        return value

    def load_compose_spec(self) -> dict[str, Any]:
        if not self.compose_file.exists():
            raise RuntimeError(f"Compose file not found: {self.compose_file}")
        payload = yaml.safe_load(self.compose_file.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError(f"Compose file is invalid: {self.compose_file}")
        expanded = self._expand_value(payload, self._compose_env())
        if not isinstance(expanded, dict):
            raise RuntimeError(f"Compose file expansion failed: {self.compose_file}")
        return expanded

    def selected_services(self, services: dict[str, Any]) -> dict[str, dict[str, Any]]:
        selected_apps = self.selected_app_set()
        selected_profiles = {item for item in self.compose_profiles if item}
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
            str(item).strip() for item in self.edge_router_service_names if str(item).strip()
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

    @staticmethod
    def service_order(services: dict[str, dict[str, Any]]) -> list[str]:
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

    def container_name(self, service_name: str, spec: dict[str, Any]) -> str:
        explicit = str(spec.get("container_name") or "").strip()
        if explicit:
            return explicit
        return f"{self.project_name()}_{service_name}_1"
