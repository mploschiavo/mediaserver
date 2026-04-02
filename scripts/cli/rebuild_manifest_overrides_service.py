"""Manifest override/apply helpers for rebuild/bootstrap orchestration."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

RunKubectlFn = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class RebuildManifestOverridesConfig:
    namespace: str
    prepare_host_root: str
    ingress_domain: str
    pvc_storage_class: str


@dataclass
class RebuildManifestOverridesService:
    cfg: RebuildManifestOverridesConfig
    run_kubectl: RunKubectlFn

    def stream_with_manifest_overrides(self, text: str) -> str:
        out = re.sub(
            r"namespace:[ \t]*media-stack\b",
            f"namespace: {self.cfg.namespace}",
            text,
        )
        out = re.sub(
            r"(?m)^name:[ \t]*media-stack$",
            f"name: {self.cfg.namespace}",
            out,
        )
        out = out.replace("/srv/media-stack", self.cfg.prepare_host_root)
        out = re.sub(
            r"([A-Za-z0-9-]+)\.local",
            rf"\1.{self.cfg.ingress_domain}",
            out,
        )
        out = re.sub(
            r"(?m)^([ \t]*STACK_ADMIN_PASSWORD:[ \t]*).*$",
            rf'\1"{self.cfg.namespace}"',
            out,
        )
        return out

    def inject_storage_class(self, text: str) -> str:
        storage_class = self.cfg.pvc_storage_class.strip()
        if not storage_class:
            return text

        lines = text.splitlines()
        out: list[str] = []
        in_pvc = False
        in_spec = False
        inserted = False

        for line in lines:
            if re.match(r"^kind:[ \t]*PersistentVolumeClaim[ \t]*$", line):
                in_pvc = True
                in_spec = False
                inserted = False
                out.append(line)
                continue

            if re.match(r"^---[ \t]*$", line):
                if in_pvc and in_spec and not inserted:
                    out.append(f"  storageClassName: {storage_class}")
                in_pvc = False
                in_spec = False
                inserted = False
                out.append(line)
                continue

            if in_pvc and re.match(r"^[ \t]*spec:[ \t]*$", line):
                in_spec = True
                out.append(line)
                continue

            if in_pvc and in_spec and re.match(r"^[ \t]*storageClassName:[ \t]*", line):
                out.append(f"  storageClassName: {storage_class}")
                inserted = True
                continue

            if in_pvc and in_spec and not inserted and re.match(r"^[ \t]*resources:[ \t]*$", line):
                out.append(f"  storageClassName: {storage_class}")
                inserted = True

            out.append(line)

        if in_pvc and in_spec and not inserted:
            out.append(f"  storageClassName: {storage_class}")

        suffix = "\n" if text.endswith("\n") else ""
        return "\n".join(out) + suffix

    def apply_manifest_text_with_overrides(self, text: str) -> None:
        patched = self.inject_storage_class(self.stream_with_manifest_overrides(text))
        self.run_kubectl(["apply", "-f", "-"], input_text=patched)

    def apply_manifest_file_with_overrides(self, file_path: Path) -> None:
        self.apply_manifest_text_with_overrides(file_path.read_text(encoding="utf-8"))
