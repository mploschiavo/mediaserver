"""Secret value reading helpers for bootstrap orchestration."""

from __future__ import annotations

import base64
from dataclasses import dataclass

from media_stack.core.platforms.kubernetes.kube_client import KubernetesClient


@dataclass(frozen=True)
class BootstrapSecretReaderConfig:
    namespace: str


@dataclass
class BootstrapSecretReaderService:
    cfg: BootstrapSecretReaderConfig
    kube: KubernetesClient

    def read_secret_key(self, secret: str, key_name: str) -> str:
        result = self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "get",
                "secret",
                secret,
                f"-o=jsonpath={{.data.{key_name}}}",
            ],
            check=False,
        )
        if result.returncode != 0:
            return ""
        value = (result.stdout or "").strip()
        if not value:
            return ""
        try:
            return base64.b64decode(value).decode("utf-8")
        except Exception:
            return ""
