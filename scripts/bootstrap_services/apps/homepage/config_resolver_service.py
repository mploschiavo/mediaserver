"""Homepage bootstrap config resolver operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from core.exceptions import ConfigError
from core.kube import KubectlClient

LogFn = Callable[[str], None]


@dataclass(frozen=True)
class HomepageConfigResolverService:
    kube: KubectlClient
    namespace: str
    ingress_name: str
    info: LogFn

    @staticmethod
    def _set_nested_value(cfg: dict[str, Any], path: str, value: Any) -> None:
        parts = [str(part).strip() for part in str(path or "").split(".") if str(part).strip()]
        if not parts:
            raise ConfigError("bootstrap_job.config_resolver target path must be non-empty.")

        cursor: dict[str, Any] = cfg
        for segment in parts[:-1]:
            existing = cursor.get(segment)
            if existing is None:
                next_cursor: dict[str, Any] = {}
                cursor[segment] = next_cursor
                cursor = next_cursor
                continue
            if not isinstance(existing, dict):
                raise ConfigError(
                    f"Cannot set '{path}': segment '{segment}' is not an object in bootstrap config."
                )
            cursor = existing
        cursor[parts[-1]] = value

    @staticmethod
    def _resolve_ingress_host_targets(resolver_cfg: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        raw_targets = resolver_cfg.get("ingress_host_targets")
        if raw_targets is None:
            return ()
        if not isinstance(raw_targets, list):
            raise ConfigError(
                "adapter_hooks.bootstrap_job.config_resolver.ingress_host_targets must be an array."
            )

        targets: list[dict[str, Any]] = []
        for index, raw_target in enumerate(raw_targets):
            if not isinstance(raw_target, dict):
                raise ConfigError(
                    "adapter_hooks.bootstrap_job.config_resolver.ingress_host_targets"
                    f"[{index}] must be an object."
                )
            hosts_path = str(raw_target.get("hosts_path") or "").strip()
            if not hosts_path:
                raise ConfigError(
                    "adapter_hooks.bootstrap_job.config_resolver.ingress_host_targets"
                    f"[{index}].hosts_path must be non-empty."
                )
            enable_path = str(raw_target.get("enable_path") or "").strip()
            enable_value = raw_target.get("enable_value", True)
            if not isinstance(enable_value, bool):
                raise ConfigError(
                    "adapter_hooks.bootstrap_job.config_resolver.ingress_host_targets"
                    f"[{index}].enable_value must be a boolean."
                )
            label = str(raw_target.get("name") or hosts_path).strip() or hosts_path
            targets.append(
                {
                    "name": label,
                    "hosts_path": hosts_path,
                    "enable_path": enable_path,
                    "enable_value": enable_value,
                }
            )
        return tuple(targets)

    def _resolve_ingress_hosts(self) -> list[str]:
        hosts_result = self.kube.run(
            [
                "-n",
                self.namespace,
                "get",
                "ingress",
                self.ingress_name,
                "-o",
                "jsonpath={range .spec.rules[*]}{.host}{'\\n'}{end}",
            ],
            check=False,
        )
        hosts: list[str] = []
        if hosts_result.returncode == 0:
            for line in (hosts_result.stdout or "").splitlines():
                host = line.strip()
                if host:
                    hosts.append(host)
        return sorted(set(hosts))

    def inject_ingress_hosts(self, cfg: dict[str, Any], *, resolver_cfg: dict[str, Any]) -> None:
        host_targets = self._resolve_ingress_host_targets(resolver_cfg)
        if not host_targets:
            self.info(
                "No ingress host injection targets configured for "
                "inject_homepage_ingress_hosts; using bootstrap config as-is."
            )
            return

        hosts = self._resolve_ingress_hosts()
        hosts_csv = ",".join(hosts)
        if hosts_csv:
            self.info(f"Discovered ingress hosts from ingress/{self.ingress_name}: {hosts_csv}")
            for target in host_targets:
                hosts_path = str(target.get("hosts_path") or "").strip()
                if not hosts_path:
                    continue
                self._set_nested_value(cfg, hosts_path, list(hosts))
                enable_path = str(target.get("enable_path") or "").strip()
                if enable_path:
                    self._set_nested_value(cfg, enable_path, bool(target.get("enable_value", True)))
                self.info(
                    f"Injected ingress hosts into config target '{target.get('name')}' "
                    f"(hosts_path={hosts_path})."
                )
            return

        self.info(
            f"No ingress hosts discovered from ingress/{self.ingress_name}; "
            "using bootstrap config defaults."
        )


def inject_ingress_hosts(
    cfg: dict[str, Any],
    *,
    resolver_cfg: dict[str, Any],
    kube: KubectlClient,
    namespace: str,
    ingress_name: str,
    info: LogFn,
) -> None:
    HomepageConfigResolverService(
        kube=kube,
        namespace=namespace,
        ingress_name=ingress_name,
        info=info,
    ).inject_ingress_hosts(cfg, resolver_cfg=resolver_cfg)
