"""Secret priming helpers for bootstrap job orchestration."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from core.exceptions import KubernetesError
from core.kube import KubectlClient

LogFn = Callable[[str], None]


@dataclass(frozen=True)
class BootstrapSecretPrimingConfig:
    namespace: str
    secret_name: str = "media-stack-secrets"
    bootstrap_config_file: Path | None = None


@dataclass
class BootstrapSecretPrimingService:
    cfg: BootstrapSecretPrimingConfig
    kube: KubectlClient
    info: LogFn
    warn: LogFn

    DEFAULT_API_KEY_APPS = ("sonarr", "radarr", "lidarr", "readarr", "prowlarr")

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

    def _read_sab_api_key_from_deploy(self) -> str:
        command = (
            "sed -n 's/^[[:space:]]*api_key[[:space:]]*=[[:space:]]*//p' /config/sabnzbd.ini "
            "| head -n1"
        )
        result = self.kube.run(
            ["-n", self.cfg.namespace, "exec", "deploy/sabnzbd", "--", "sh", "-c", command],
            check=False,
        )
        if result.returncode != 0:
            return ""
        return self._clean(result.stdout)

    def _read_jellyseerr_api_key_from_deploy(self) -> str:
        command = (
            "node -e \"const fs=require('fs'); "
            "const d=JSON.parse(fs.readFileSync('/app/config/settings.json','utf8')); "
            "process.stdout.write(String(((d.main||{}).apiKey||'')).trim());\""
        )
        result = self.kube.run(
            ["-n", self.cfg.namespace, "exec", "deploy/jellyseerr", "--", "sh", "-c", command],
            check=False,
        )
        if result.returncode != 0:
            return ""
        return self._clean(result.stdout)

    def _read_tautulli_api_key_from_deploy(self) -> str:
        command = (
            "sed -n 's/^[[:space:]]*api_key[[:space:]]*=[[:space:]]*//p' /config/config.ini "
            "| head -n1"
        )
        result = self.kube.run(
            ["-n", self.cfg.namespace, "exec", "deploy/tautulli", "--", "sh", "-c", command],
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
        path = self.cfg.bootstrap_config_file
        if path and path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # pragma: no cover - defensive fallback
                self.warn(
                    f"Could not parse bootstrap config at {path}; "
                    f"falling back to default Arr key priming list ({exc})."
                )
            else:
                apps: list[str] = []
                arr_apps = payload.get("arr_apps") if isinstance(payload, dict) else None
                if isinstance(arr_apps, list):
                    for item in arr_apps:
                        if not isinstance(item, dict):
                            continue
                        app = self._normalize_deploy_token(
                            item.get("implementation") or item.get("name")
                        )
                        if app and app not in apps:
                            apps.append(app)
                prowlarr_url = str((payload or {}).get("prowlarr_url") or "").strip()
                if prowlarr_url and "prowlarr" not in apps:
                    apps.append("prowlarr")
                if apps:
                    return apps

        return list(self.DEFAULT_API_KEY_APPS)

    def prime_servarr_api_keys(self) -> None:
        if not self._secret_exists():
            self.warn(
                f"Secret {self.cfg.namespace}/{self.cfg.secret_name} not found; "
                "skipping Arr API key priming."
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
            self.warn("No Arr/Prowlarr API keys were discovered from running deployments.")
        else:
            self.info(f"Primed API keys in secret for {found} app(s).")

    def prime_sab_api_key(self) -> None:
        if not self._secret_exists():
            self.warn(
                f"Secret {self.cfg.namespace}/{self.cfg.secret_name} not found; "
                "skipping SABnzbd API key priming."
            )
            return

        key = self._clean(os.environ.get("SABNZBD_API_KEY"))
        if not key:
            key = self._read_sab_api_key_from_deploy()
        if not key:
            self.warn("Could not discover SABnzbd API key from env or deploy/sabnzbd; continuing.")
            return

        self._patch_secret_string("SABNZBD_API_KEY", key)
        self.info("Seeded SABNZBD_API_KEY in media-stack-secrets.")

    def prime_jellyseerr_api_key(self) -> None:
        if not self._secret_exists():
            self.warn(
                f"Secret {self.cfg.namespace}/{self.cfg.secret_name} not found; "
                "skipping Jellyseerr API key priming."
            )
            return

        key = self._clean(os.environ.get("JELLYSEERR_API_KEY"))
        if not key:
            key = self._read_jellyseerr_api_key_from_deploy()
        if not key:
            self.warn(
                "Could not discover Jellyseerr API key from env or deploy/jellyseerr; continuing."
            )
            return

        self._patch_secret_string("JELLYSEERR_API_KEY", key)
        self.info("Seeded JELLYSEERR_API_KEY in media-stack-secrets.")

    def prime_tautulli_api_key(self) -> None:
        if not self._secret_exists():
            self.warn(
                f"Secret {self.cfg.namespace}/{self.cfg.secret_name} not found; "
                "skipping Tautulli API key priming."
            )
            return

        key = self._clean(os.environ.get("TAUTULLI_API_KEY"))
        if not key:
            key = self._read_tautulli_api_key_from_deploy()
        if not key:
            self.warn(
                "Could not discover Tautulli API key from env or deploy/tautulli; continuing."
            )
            return

        self._patch_secret_string("TAUTULLI_API_KEY", key)
        self.info("Seeded TAUTULLI_API_KEY in media-stack-secrets.")
