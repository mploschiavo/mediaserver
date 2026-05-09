"""Secret priming helpers for bootstrap job orchestration."""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from media_stack.core.exceptions import ConfigError, KubernetesError
from media_stack.core.platforms.kubernetes.kube_client import KubernetesClient
import logging

LogFn = Callable[[str], None]


@dataclass(frozen=True)
class ControllerSecretPrimingConfig:
    namespace: str
    secret_name: str = "media-stack-secrets"
    bootstrap_config_file: Path | None = None


@dataclass
class ControllerSecretPrimingService:
    cfg: ControllerSecretPrimingConfig
    kube: KubernetesClient
    info: LogFn
    warn: LogFn

    @staticmethod
    def _clean(value: str | None) -> str:
        return (value or "").replace("\r", "").replace("\n", "").strip()

    def _secret_exists(self) -> bool:
        return (
            self.kube.run(
                ["-n", self.cfg.namespace, "get", "secret", self.cfg.secret_name],
                check=False,
            ).returncode
            == 0
        )

    def _read_api_key_from_deploy(self, app: str) -> str:
        command = "sed -n 's:.*<ApiKey>\\(.*\\)</ApiKey>.*:\\1:p' /config/config.xml " "| head -n1"
        result = self.kube.run(
            ["-n", self.cfg.namespace, "exec", f"deploy/{app}", "--", "sh", "-c", command],
            check=False,
        )
        if result.returncode != 0:
            return ""
        return self._clean(result.stdout)

    def _read_value_from_deploy(self, deployment: str, command: str) -> str:
        token = self._normalize_deploy_token(deployment)
        if not token:
            return ""
        command_text = str(command or "").strip()
        if not command_text:
            return ""
        result = self.kube.run(
            ["-n", self.cfg.namespace, "exec", f"deploy/{token}", "--", "sh", "-c", command_text],
            check=False,
        )
        if result.returncode != 0:
            return ""
        return self._clean(result.stdout)

    def _patch_secret_string(self, key_name: str, key_value: str) -> None:
        if not key_name or not key_value:
            return
        payload = json.dumps({"stringData": {key_name: key_value}})
        result = self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "patch",
                "secret",
                self.cfg.secret_name,
                "--type",
                "merge",
                "-p",
                payload,
            ],
            check=False,
        )
        if result.returncode != 0:
            raise KubernetesError(result.stderr or result.stdout)

    @staticmethod
    def _normalize_deploy_token(value: str | None) -> str:
        token = str(value or "").strip().lower()
        token = re.sub(r"[^a-z0-9-]+", "-", token)
        token = token.strip("-")
        return token

    @staticmethod
    def _api_key_env_name(app: str) -> str:
        token = re.sub(r"[^A-Za-z0-9]+", "_", str(app or ""))
        token = token.strip("_").upper()
        if not token:
            return ""
        return f"{token}_API_KEY"

    def _resolve_api_key_apps(self) -> list[str]:
        # 1. If config.json has explicit arr_api_key_technologies, use it
        path = self.cfg.bootstrap_config_file
        if path and path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                adapter_hooks = payload.get("adapter_hooks")
                bootstrap_job_hooks = (
                    adapter_hooks.get("bootstrap_job") if isinstance(adapter_hooks, dict) else {}
                )
                configured_tokens = (
                    bootstrap_job_hooks.get("arr_api_key_technologies")
                    if isinstance(bootstrap_job_hooks, dict)
                    else None
                )
                if isinstance(configured_tokens, list):
                    apps: list[str] = []
                    for item in configured_tokens:
                        app = self._normalize_deploy_token(item)
                        if app and app not in apps:
                            apps.append(app)
                    if apps:
                        return apps
            except Exception as exc:
                log_swallowed(exc)

        # 2. Derive from per-service YAML registry (category=automation)
        try:
            from media_stack.core.service_registry.registry import SERVICES
            apps = [s.id for s in SERVICES if s.category == "automation"]
            if apps:
                return apps
        except Exception as exc:
            log_swallowed(exc)

        raise ConfigError(
            "Could not resolve API-key app list: "
            "no arr_api_key_technologies in config and service registry unavailable."
        )

    def _resolve_secret_priming_targets(self) -> dict[str, dict[str, str]]:
        path = self.cfg.bootstrap_config_file
        if not path or not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ConfigError(f"Could not parse bootstrap config at {path}: {exc}") from exc

        adapter_hooks = payload.get("adapter_hooks")
        if not isinstance(adapter_hooks, dict):
            return {}
        bootstrap_job = adapter_hooks.get("bootstrap_job")
        if not isinstance(bootstrap_job, dict):
            return {}
        raw_targets = bootstrap_job.get("secret_priming_targets")
        if raw_targets is None:
            return {}
        if not isinstance(raw_targets, dict):
            raise ConfigError(
                "adapter_hooks.bootstrap_job.secret_priming_targets must be an object"
            )

        targets: dict[str, dict[str, str]] = {}
        for key, value in raw_targets.items():
            token = str(key or "").strip()
            if not token:
                continue
            if not isinstance(value, dict):
                raise ConfigError(
                    "adapter_hooks.bootstrap_job.secret_priming_targets"
                    f".{token} must be an object"
                )
            env_key = str(value.get("env_key") or "").strip()
            deployment = str(value.get("deployment") or "").strip()
            extract_command = str(value.get("extract_command") or "").strip()
            env_var = str(value.get("env_var") or "").strip() or env_key
            if not env_key or not deployment or not extract_command:
                raise ConfigError(
                    "adapter_hooks.bootstrap_job.secret_priming_targets"
                    f".{token} requires env_key, deployment, and extract_command"
                )
            targets[token] = {
                "env_key": env_key,
                "deployment": deployment,
                "extract_command": extract_command,
                "env_var": env_var,
            }
        return targets

    def _prime_named_target(self, target_key: str) -> None:
        targets = self._resolve_secret_priming_targets()
        target = targets.get(target_key)
        if not target:
            self.warn(
                "Secret priming target not configured: "
                f"adapter_hooks.bootstrap_job.secret_priming_targets.{target_key}"
            )
            return

        if not self._secret_exists():
            self.warn(
                f"Secret {self.cfg.namespace}/{self.cfg.secret_name} not found; "
                f"skipping {target.get('env_key', target_key)} priming."
            )
            return

        env_var = str(target.get("env_var") or "").strip()
        env_key = str(target.get("env_key") or "").strip()
        deployment = str(target.get("deployment") or "").strip()
        extract_command = str(target.get("extract_command") or "").strip()
        if not env_key:
            self.warn(f"Skipping secret priming target '{target_key}': missing env_key")
            return

        key = self._clean(os.environ.get(env_var))
        if not key:
            key = self._read_value_from_deploy(deployment, extract_command)
        if not key:
            self.warn(
                f"Could not discover {env_key} from env/{env_var} or deploy/{deployment}; continuing."
            )
            return

        self._patch_secret_string(env_key, key)
        self.info(f"Seeded {env_key} in media-stack-secrets.")

    def prime_servarr_api_keys(self) -> None:
        if not self._secret_exists():
            self.warn(
                f"Secret {self.cfg.namespace}/{self.cfg.secret_name} not found; "
                "skipping component API key priming."
            )
            return

        apps = self._resolve_api_key_apps()
        found = 0
        for app in apps:
            key = self._read_api_key_from_deploy(app)
            if not key:
                self.warn(f"Could not read API key from deploy/{app} yet; continuing.")
                continue
            env_key = self._api_key_env_name(app)
            if not env_key:
                self.warn(f"Skipping API key seed for invalid app token '{app}'.")
                continue
            self._patch_secret_string(env_key, key)
            self.info(f"Seeded {env_key} in media-stack-secrets from deploy/{app}")
            found += 1

        if found == 0:
            self.warn("No configured API keys were discovered from running deployments.")
        else:
            self.info(f"Primed API keys in secret for {found} app(s).")

    def prime_usenet_client_api_key(self) -> None:
        self._prime_named_target("usenet_client")

    def prime_request_manager_api_key(self) -> None:
        self._prime_named_target("request_manager")

    def prime_analytics_api_key(self) -> None:
        self._prime_named_target("analytics")

    def prime_media_server_api_key(self) -> None:
        self._prime_named_target("media_server_api_key")

    def prime_media_server_user_id(self) -> None:
        self._prime_named_target("media_server_user_id")
