"""Secret backup/restore helpers for rebuild/bootstrap orchestration."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Callable

InfoFn = Callable[[str], None]
RunKubeFn = Callable[..., object]


@dataclass(frozen=True)
class RebuildSecretPreservationConfig:
    namespace: str
    secret_name: str
    preserve_keys: tuple[str, ...] = ()


@dataclass
class RebuildSecretPreservationService:
    cfg: RebuildSecretPreservationConfig
    info: InfoFn
    run_kube: RunKubeFn

    def backup_existing_values(self, preserve_secret_on_rebuild: str) -> dict[str, str]:
        if preserve_secret_on_rebuild != "1":
            self.info("Secret preservation disabled (PRESERVE_SECRET_ON_REBUILD=0).")
            return {}

        proc = self.run_kube(
            [
                "-n",
                self.cfg.namespace,
                "get",
                "secret",
                self.cfg.secret_name,
                "-o",
                "json",
            ],
            check=False,
        )
        if proc.returncode != 0:
            self.info(
                f"No existing secret {self.cfg.namespace}/{self.cfg.secret_name} found to preserve."
            )
            return {}

        payload = json.loads(proc.stdout or "{}")
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            data = {}

        keys = tuple(self.cfg.preserve_keys or ("STACK_ADMIN_USERNAME", "STACK_ADMIN_PASSWORD"))

        restored: dict[str, str] = {}
        for key in keys:
            encoded = str(data.get(key) or "").strip()
            if not encoded:
                continue
            try:
                decoded = base64.b64decode(encoded).decode("utf-8")
            except Exception as exc:
                import logging; logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
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

        exists = self.run_kube(
            [
                "-n",
                self.cfg.namespace,
                "get",
                "secret",
                self.cfg.secret_name,
            ],
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
            self.run_kube(["apply", "-f", "-"], input_text=manifest)

        patch_payload = json.dumps({"stringData": values})
        self.run_kube(
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
