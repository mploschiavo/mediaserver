"""Secret backup/restore helpers for rebuild/bootstrap orchestration."""

from __future__ import annotations

import base64
import json
import subprocess
from dataclasses import dataclass
from typing import Callable

InfoFn = Callable[[str], None]
RunKubectlFn = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class RebuildSecretPreservationConfig:
    namespace: str
    secret_name: str
    kubectl: list[str]


@dataclass
class RebuildSecretPreservationService:
    cfg: RebuildSecretPreservationConfig
    info: InfoFn
    run_kubectl: RunKubectlFn

    def backup_existing_values(self, preserve_secret_on_rebuild: str) -> dict[str, str]:
        if preserve_secret_on_rebuild != "1":
            self.info("Secret preservation disabled (PRESERVE_SECRET_ON_REBUILD=0).")
            return {}

        proc = subprocess.run(
            [
                *self.cfg.kubectl,
                "-n",
                self.cfg.namespace,
                "get",
                "secret",
                self.cfg.secret_name,
                "-o",
                "json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            self.info(f"No existing secret {self.cfg.namespace}/{self.cfg.secret_name} found to preserve.")
            return {}

        payload = json.loads(proc.stdout or "{}")
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            data = {}

        keys = [
            "SABNZBD_API_KEY",
            "STACK_ADMIN_USERNAME",
            "STACK_ADMIN_PASSWORD",
            "JELLYFIN_API_KEY",
            "JELLYFIN_USER_ID",
        ]

        restored: dict[str, str] = {}
        for key in keys:
            encoded = str(data.get(key) or "").strip()
            if not encoded:
                continue
            try:
                decoded = base64.b64decode(encoded).decode("utf-8")
            except Exception:
                continue
            if decoded:
                restored[key] = decoded

        if restored:
            self.info(
                f"Backed up {len(restored)} secret key(s) from "
                f"{self.cfg.namespace}/{self.cfg.secret_name}."
            )
        else:
            self.info(
                f"Secret {self.cfg.namespace}/{self.cfg.secret_name} exists but "
                "has no matching keys to preserve."
            )
        return restored

    def restore_values(self, values: dict[str, str]) -> None:
        if not values:
            self.info("No preserved secret values to restore.")
            return

        exists = subprocess.run(
            [
                *self.cfg.kubectl,
                "-n",
                self.cfg.namespace,
                "get",
                "secret",
                self.cfg.secret_name,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if exists.returncode != 0:
            self.info(
                f"Secret {self.cfg.namespace}/{self.cfg.secret_name} missing after apply; "
                "creating it before restore."
            )
            manifest = (
                "apiVersion: v1\n"
                "kind: Secret\n"
                "metadata:\n"
                f"  name: {self.cfg.secret_name}\n"
                f"  namespace: {self.cfg.namespace}\n"
                "type: Opaque\n"
                "stringData: {}\n"
            )
            self.run_kubectl(["apply", "-f", "-"], input_text=manifest)

        patch_payload = json.dumps({"stringData": values})
        self.run_kubectl(
            [
                "-n",
                self.cfg.namespace,
                "patch",
                "secret",
                self.cfg.secret_name,
                "--type",
                "merge",
                "-p",
                patch_payload,
            ]
        )
        self.info(f"Restored preserved values into {self.cfg.namespace}/{self.cfg.secret_name}.")
