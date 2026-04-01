"""Jellyfin plugin activation orchestration."""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from typing import Callable

from core.kube import KubernetesClient

LogFn = Callable[[str], None]
DeploymentExistsFn = Callable[[str], bool]
RestartDeploymentFn = Callable[[str, int], None]
ReadSecretKeyFn = Callable[[str, str], str]


@dataclass(frozen=True)
class JellyfinPluginActivationConfig:
    namespace: str
    secret_name: str = "media-stack-secrets"
    api_key_secret_key: str = "JELLYFIN_API_KEY"


@dataclass
class JellyfinPluginActivationService:
    cfg: JellyfinPluginActivationConfig
    kube: KubernetesClient
    info: LogFn
    warn: LogFn
    deployment_exists: DeploymentExistsFn
    restart_deployment: RestartDeploymentFn
    read_secret_key: ReadSecretKeyFn

    def activate_plugins_if_needed(self) -> None:
        if not self.deployment_exists("jellyfin"):
            self.info(
                f"deployment/jellyfin not found in namespace/{self.cfg.namespace}; "
                "skipping restart check."
            )
            return

        jellyfin_api_key = self.read_secret_key(
            self.cfg.secret_name,
            self.cfg.api_key_secret_key,
        )
        if not jellyfin_api_key:
            self.info(
                f"{self.cfg.api_key_secret_key} not found in secret; "
                "skipping Jellyfin plugin activation restart."
            )
            return

        plugins_url = f"http://localhost:8096/Plugins?api_key={jellyfin_api_key}"
        command = f"curl -fsS {shlex.quote(plugins_url)}"
        result = self.kube.run(
            ["-n", self.cfg.namespace, "exec", "deploy/jellyfin", "--", "sh", "-lc", command],
            check=False,
        )
        if result.returncode != 0:
            self.warn("Could not query Jellyfin plugins; skipping plugin activation restart check.")
            return

        restart_count = 0
        try:
            payload = json.loads(result.stdout or "[]")
            if isinstance(payload, list):
                restart_count = sum(
                    1
                    for item in payload
                    if isinstance(item, dict) and item.get("Status") == "Restart"
                )
        except json.JSONDecodeError:
            restart_count = (result.stdout or "").count('"Status":"Restart"')

        if restart_count > 0:
            self.info(
                f"Detected {restart_count} Jellyfin plugin(s) pending restart; "
                "restarting deployment/jellyfin."
            )
            self.restart_deployment("jellyfin", 300)
            self.info("Jellyfin restarted to activate pending plugin changes.")
        else:
            self.info("No Jellyfin plugin restart pending.")
