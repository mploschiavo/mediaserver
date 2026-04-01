"""Resolve bootstrap job config from cluster/runtime context."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from bootstrap_services.top_level_config_model import TopLevelBootstrapConfig
from core.exceptions import ConfigError
from core.kube import KubectlClient

LogFn = Callable[[str], None]


@dataclass(frozen=True)
class BootstrapConfigResolverConfig:
    namespace: str
    ingress_name: str
    config_file: Path
    job_config_file: Path


@dataclass
class BootstrapConfigResolverService:
    cfg: BootstrapConfigResolverConfig
    kube: KubectlClient
    info: LogFn

    def _load_json(self, path: Path) -> dict[str, Any]:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ConfigError(f"Expected JSON object in {path}")
        try:
            return TopLevelBootstrapConfig.from_dict(data).to_dict()
        except ValueError as exc:
            raise ConfigError(f"Invalid bootstrap config at {path}: {exc}") from exc

    def resolve_bootstrap_config(self) -> None:
        hosts_result = self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "get",
                "ingress",
                self.cfg.ingress_name,
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
        hosts = sorted(set(hosts))
        hosts_csv = ",".join(hosts)
        if hosts_csv:
            self.info(f"Injecting homepage hosts from ingress/{self.cfg.ingress_name}: {hosts_csv}")
        else:
            self.info(
                f"No ingress hosts discovered from ingress/{self.cfg.ingress_name}; "
                "using bootstrap config defaults."
            )

        cfg = self._load_json(self.cfg.config_file)
        if hosts:
            homepage = cfg.get("homepage")
            if not isinstance(homepage, dict):
                homepage = {}
            homepage["enabled"] = True
            homepage["hosts"] = hosts
            cfg["homepage"] = homepage

        self.cfg.job_config_file.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        self.info(f"Resolved job config: {self.cfg.job_config_file}")
