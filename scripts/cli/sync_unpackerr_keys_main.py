#!/usr/bin/env python3
"""Sync Arr/Prowlarr API keys into media-stack-secrets."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from core.exceptions import ConfigError, KubernetesError, MediaStackError
from core.kube import KubectlClient
from core.logging_utils import configure_logging, log_event

API_KEY_RE = re.compile(r"<ApiKey>(.*?)</ApiKey>")


@dataclass(frozen=True)
class SyncUnpackerrKeysConfig:
    namespace: str
    secret_name: str = "media-stack-secrets"


class SyncUnpackerrKeysService:
    def __init__(
        self,
        cfg: SyncUnpackerrKeysConfig,
        kube: KubectlClient,
        logger: logging.Logger,
    ) -> None:
        self.cfg = cfg
        self.kube = kube
        self.logger = logger

    def run(self) -> int:
        keys = {
            "sonarr": self._read_api_key("sonarr"),
            "radarr": self._read_api_key("radarr"),
            "lidarr": self._read_api_key("lidarr"),
            "readarr": self._read_api_key("readarr"),
            "prowlarr": self._read_api_key("prowlarr"),
        }
        missing = [name for name, value in keys.items() if not value]
        if missing:
            raise ConfigError(
                "One or more API keys were empty. Ensure Arr apps are healthy first. "
                f"Missing: {', '.join(missing)}"
            )

        secret_manifest = self._build_secret_manifest(keys)
        self._apply_manifest(secret_manifest)
        restarted = self._restart_unpackerr_if_active()

        print(
            f"[OK] Updated secret {self.cfg.namespace}/{self.cfg.secret_name} "
            "with Arr/Prowlarr API keys."
        )
        if restarted:
            print(f"[OK] Restarted deploy/unpackerr in namespace {self.cfg.namespace}.")
        else:
            print("Enable/restart Unpackerr:")
            print(f"  kubectl -n {self.cfg.namespace} apply -f k8s/unpackerr.yaml")
            print(f"  kubectl -n {self.cfg.namespace} scale deploy/unpackerr --replicas=1")
        return 0

    def _read_api_key(self, app: str) -> str:
        result = self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "exec",
                f"deploy/{app}",
                "--",
                "sh",
                "-lc",
                "cat /config/config.xml",
            ]
        )
        text = result.stdout.strip()
        match = API_KEY_RE.search(text)
        value = match.group(1).strip() if match else ""
        log_event(
            self.logger,
            logging.INFO,
            "sync.unpackerr.read_key",
            app=app,
            namespace=self.cfg.namespace,
            key_present=bool(value),
        )
        return value

    def _build_secret_manifest(self, keys: dict[str, str]) -> str:
        string_data = {
            "SONARR_API_KEY": keys["sonarr"],
            "RADARR_API_KEY": keys["radarr"],
            "LIDARR_API_KEY": keys["lidarr"],
            "READARR_API_KEY": keys["readarr"],
            "PROWLARR_API_KEY": keys["prowlarr"],
        }

        lines = [
            "apiVersion: v1",
            "kind: Secret",
            "metadata:",
            f"  name: {self.cfg.secret_name}",
            f"  namespace: {self.cfg.namespace}",
            "type: Opaque",
            "stringData:",
        ]
        for key, value in string_data.items():
            lines.append(f"  {key}: {json.dumps(value)}")
        lines.append("")
        return "\n".join(lines)

    def _apply_manifest(self, manifest_text: str) -> None:
        with tempfile.TemporaryDirectory(prefix="media-stack-sync-keys-") as tmpdir:
            manifest_path = Path(tmpdir) / "secret.yaml"
            manifest_path.write_text(manifest_text, encoding="utf-8")
            self.kube.run(["apply", "-f", str(manifest_path)])
        log_event(
            self.logger,
            logging.INFO,
            "sync.unpackerr.secret_applied",
            namespace=self.cfg.namespace,
            secret=self.cfg.secret_name,
        )

    def _restart_unpackerr_if_active(self) -> bool:
        result = self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "get",
                "deploy/unpackerr",
                "-o",
                "jsonpath={.spec.replicas}",
            ],
            check=False,
        )
        if result.returncode != 0:
            log_event(
                self.logger,
                logging.INFO,
                "sync.unpackerr.deployment_missing",
                namespace=self.cfg.namespace,
            )
            return False

        replicas_text = str(result.stdout or "").strip()
        try:
            replicas = int(replicas_text or "0")
        except ValueError:
            replicas = 0
        if replicas <= 0:
            log_event(
                self.logger,
                logging.INFO,
                "sync.unpackerr.deployment_scaled_zero",
                namespace=self.cfg.namespace,
                replicas=replicas,
            )
            return False

        self.kube.run(
            ["-n", self.cfg.namespace, "rollout", "restart", "deploy/unpackerr"]
        )
        rollout_result = self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "rollout",
                "status",
                "deploy/unpackerr",
                "--timeout=90s",
            ],
            check=False,
        )
        if rollout_result.returncode != 0:
            log_event(
                self.logger,
                logging.WARNING,
                "sync.unpackerr.rollout_not_ready",
                namespace=self.cfg.namespace,
                stderr=(rollout_result.stderr or "").strip(),
            )
        log_event(
            self.logger,
            logging.INFO,
            "sync.unpackerr.restarted",
            namespace=self.cfg.namespace,
            replicas=replicas,
        )
        return True


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts/sync-unpackerr-keys.sh",
        description=(
            "Reads Sonarr/Radarr/Lidarr/Readarr/Prowlarr API keys from running pods and "
            "updates media-stack-secrets."
        ),
    )
    parser.add_argument("--namespace", default="media-stack")
    parser.add_argument("--secret-name", default="media-stack-secrets")
    return parser


def parse_config(argv: list[str] | None = None) -> SyncUnpackerrKeysConfig:
    args = build_arg_parser().parse_args(argv)
    namespace = str(args.namespace or "").strip()
    secret_name = str(args.secret_name or "").strip()
    if not namespace:
        raise ConfigError("namespace must be non-empty")
    if not secret_name:
        raise ConfigError("secret name must be non-empty")
    return SyncUnpackerrKeysConfig(namespace=namespace, secret_name=secret_name)


def main(argv: list[str] | None = None) -> int:
    logger = configure_logging()
    try:
        cfg = parse_config(argv)
        service = SyncUnpackerrKeysService(
            cfg=cfg,
            kube=KubectlClient.from_environment(),
            logger=logger,
        )
        return service.run()
    except (ConfigError, KubernetesError, MediaStackError) as exc:
        log_event(logger, logging.ERROR, "sync.unpackerr.failed", error=str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
