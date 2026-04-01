from __future__ import annotations

import argparse
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class JellyfinBootstrapConfig:
    namespace: str
    secret_name: str
    service_name: str
    wait_seconds: int
    app_name: str


def parse_jellyfin_bootstrap_config(argv=None) -> JellyfinBootstrapConfig:
    parser = argparse.ArgumentParser(
        prog="scripts/ensure-jellyfin-bootstrap.sh",
        description=(
            "Completes Jellyfin first-run bootstrap and syncs API key/user id into media-stack secret."
        ),
    )
    parser.parse_args(argv)

    return JellyfinBootstrapConfig(
        namespace=os.environ.get("NAMESPACE", "media-stack"),
        secret_name=os.environ.get("SECRET_NAME", "media-stack-secrets"),
        service_name=os.environ.get("JELLYFIN_SERVICE_NAME", "jellyfin"),
        wait_seconds=int(os.environ.get("JELLYFIN_BOOTSTRAP_WAIT_SECONDS", "180")),
        app_name=os.environ.get("JELLYFIN_API_KEY_APP_NAME", "media-stack-bootstrap"),
    )
