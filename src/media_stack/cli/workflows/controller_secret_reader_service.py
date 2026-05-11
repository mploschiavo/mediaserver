"""Secret value reading helpers for bootstrap orchestration."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass

from media_stack.core.platforms.kubernetes.kube_client import KubernetesClient


@dataclass(frozen=True)
class ControllerSecretReaderConfig:
    namespace: str


@dataclass
class ControllerSecretReaderService:
    cfg: ControllerSecretReaderConfig
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
        except (binascii.Error, ValueError, UnicodeDecodeError):
            # binascii.Error: malformed base64 (corrupt secret).
            # ValueError: same family, Python version-dependent.
            # UnicodeDecodeError: secret bytes aren't UTF-8 (binary
            # secret stored where a string was expected). All three
            # collapse to "no usable secret" — caller treats empty
            # string as absent.
            return ""
