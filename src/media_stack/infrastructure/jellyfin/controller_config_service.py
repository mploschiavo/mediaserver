from __future__ import annotations

import argparse
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class JellyfinBootstrapConfig:
    namespace: str
    secret_name: str
    service_name: str
    wait_seconds: int
    app_name: str


class JellyfinBootstrapConfigService:
    """Parses Jellyfin bootstrap CLI args + env into a frozen config object."""

    _DEFAULT_NAMESPACE = "media-stack"
    _DEFAULT_SECRET_NAME = "media-stack-secrets"
    _DEFAULT_SERVICE_NAME = "jellyfin"
    _DEFAULT_WAIT_SECONDS = "180"
    _DEFAULT_APP_NAME = "media-stack-controller"

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env: Mapping[str, str] = env if env is not None else os.environ

    def parse(self, argv: Sequence[str] | None = None) -> JellyfinBootstrapConfig:
        parser = self._build_parser()
        parser.parse_args(argv)
        return JellyfinBootstrapConfig(
            namespace=self._env.get("NAMESPACE", self._DEFAULT_NAMESPACE),
            secret_name=self._env.get("SECRET_NAME", self._DEFAULT_SECRET_NAME),
            service_name=self._env.get("JELLYFIN_SERVICE_NAME", self._DEFAULT_SERVICE_NAME),
            wait_seconds=int(
                self._env.get("JELLYFIN_BOOTSTRAP_WAIT_SECONDS", self._DEFAULT_WAIT_SECONDS)
            ),
            app_name=self._env.get("JELLYFIN_API_KEY_APP_NAME", self._DEFAULT_APP_NAME),
        )

    def _build_parser(self) -> argparse.ArgumentParser:
        return argparse.ArgumentParser(
            prog="bin/ensure-jellyfin-bootstrap.sh",
            description=(
                "Completes Jellyfin first-run bootstrap and syncs API key/user id into media-stack secret."
            ),
        )


def parse_jellyfin_bootstrap_config(
    argv: Sequence[str] | None = None,
) -> JellyfinBootstrapConfig:
    """Backward-compatible alias delegating to ``JellyfinBootstrapConfigService``."""
    return JellyfinBootstrapConfigService().parse(argv)
