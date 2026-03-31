from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RebuildBootstrapConfig:
    root_dir: Path
    namespace: str = "media-stack"
    secret_name: str = "media-stack-secrets"
    wait_timeout: str = "20m"
    delete_namespace: str = "1"
    include_optional: str = ""
    enable_unpackerr: str = ""
    run_bootstrap: str = ""
    run_smoke_test: str = "1"
    skip_prepare_host: str = "0"
    prepare_host_root: str = "/srv/media-stack"
    storage_mode: str = "dynamic-pvc"
    pvc_storage_class: str = ""
    ingress_domain: str = "local"
    config_file: Path = Path("bootstrap/media-stack.bootstrap.json")
    ingress_class: str = "auto"
    profile: str = "full"
    alert_webhook_url: str = ""
    generate_secrets_on_rebuild: str = "0"
    preserve_secret_on_rebuild: str = "1"
    node_ip: str = ""


def parse_rebuild_bootstrap_config(argv: list[str], *, root_dir: Path) -> RebuildBootstrapConfig:
    parser = argparse.ArgumentParser(
        prog="scripts/rebuild-and-bootstrap.sh",
        description="Full automation helper for media-stack rebuild and bootstrap.",
    )
    parser.add_argument("node_ip", nargs="?", default=os.environ.get("NODE_IP", ""))
    parser.add_argument("--namespace", default=os.environ.get("NAMESPACE", "media-stack"))
    parser.add_argument("--ingress-domain", default=os.environ.get("INGRESS_DOMAIN", "local"))
    parser.add_argument("--storage-class", default=os.environ.get("PVC_STORAGE_CLASS", ""))
    parsed = parser.parse_args(argv)

    return RebuildBootstrapConfig(
        root_dir=root_dir,
        namespace=parsed.namespace,
        secret_name=os.environ.get("SECRET_NAME", "media-stack-secrets"),
        wait_timeout=os.environ.get("WAIT_TIMEOUT", "20m"),
        delete_namespace=os.environ.get("DELETE_NAMESPACE", "1"),
        include_optional=os.environ.get("INCLUDE_OPTIONAL", ""),
        enable_unpackerr=os.environ.get("ENABLE_UNPACKERR", ""),
        run_bootstrap=os.environ.get("RUN_BOOTSTRAP", ""),
        run_smoke_test=os.environ.get("RUN_SMOKE_TEST", "1"),
        skip_prepare_host=os.environ.get("SKIP_PREPARE_HOST", "0"),
        prepare_host_root=os.environ.get("PREPARE_HOST_ROOT", "/srv/media-stack"),
        storage_mode=os.environ.get("STORAGE_MODE", "dynamic-pvc"),
        pvc_storage_class=parsed.storage_class,
        ingress_domain=parsed.ingress_domain,
        config_file=Path(
            os.environ.get("CONFIG_FILE", str(root_dir / "bootstrap" / "media-stack.bootstrap.json"))
        ),
        ingress_class=os.environ.get("INGRESS_CLASS", "auto"),
        profile=os.environ.get("PROFILE", "full"),
        alert_webhook_url=os.environ.get("ALERT_WEBHOOK_URL", ""),
        generate_secrets_on_rebuild=os.environ.get("GENERATE_SECRETS_ON_REBUILD", "0"),
        preserve_secret_on_rebuild=os.environ.get("PRESERVE_SECRET_ON_REBUILD", "1"),
        node_ip=parsed.node_ip,
    )
