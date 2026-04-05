"""Manifest override/apply helpers for rebuild/bootstrap orchestration."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml

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

    @staticmethod
    def _leading_spaces(line: str) -> int:
        return len(line) - len(line.lstrip(" "))

    @classmethod
    def _extract_named_kinds(cls, text: str, *, kind: str) -> tuple[str, ...]:
        names: list[str] = []
        docs = re.split(r"(?m)^---\s*$", str(text or ""))
        target_kind = str(kind or "").strip()
        for doc in docs:
            if not re.search(rf"(?m)^\s*kind:\s*{re.escape(target_kind)}\s*$", doc):
                continue

            in_metadata = False
            metadata_indent = 0
            for line in doc.splitlines():
                if not line.strip():
                    continue
                indent = cls._leading_spaces(line)
                if not in_metadata:
                    if re.match(r"^\s*metadata:\s*$", line):
                        in_metadata = True
                        metadata_indent = indent
                    continue
                if indent <= metadata_indent:
                    break
                match = re.match(r"^\s*name:\s*([^\s#]+)\s*$", line)
                if match:
                    name = str(match.group(1)).strip()
                    if name and name not in names:
                        names.append(name)
                    break
        return tuple(names)

    @staticmethod
    def _iter_manifest_text_docs(text: str) -> tuple[str, ...]:
        docs: list[str] = []
        for raw_doc in re.split(r"(?m)^---\s*$", str(text or "")):
            if not raw_doc.strip():
                continue
            parsed = yaml.safe_load(raw_doc)
            if not isinstance(parsed, dict):
                continue
            docs.append(yaml.safe_dump(parsed, sort_keys=False))
        return tuple(docs)

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
        # Ensure namespace exists before applying namespaced resources.
        if self.cfg.namespace:
            self.run_kubectl(
                ["create", "namespace", self.cfg.namespace],
                check=False,
            )
        for job_name in self._extract_named_kinds(patched, kind="Job"):
            self.run_kubectl(
                ["-n", self.cfg.namespace, "delete", "job", job_name, "--ignore-not-found"],
                check=False,
            )
        result = self.run_kubectl(["apply", "-f", "-"], input_text=patched, check=False)
        if result.returncode == 0:
            return

        message = f"{result.stderr or ''}\n{result.stdout or ''}".lower()
        if "conflict" not in message:
            self.run_kubectl(["apply", "-f", "-"], input_text=patched)
            return

        for doc in self._iter_manifest_text_docs(patched):
            replaced = self.run_kubectl(["replace", "-f", "-"], input_text=doc, check=False)
            if replaced.returncode == 0:
                continue
            created = self.run_kubectl(["create", "-f", "-"], input_text=doc, check=False)
            if created.returncode == 0:
                continue
            create_message = f"{created.stderr or ''}\n{created.stdout or ''}".lower()
            if "already exists" in create_message:
                continue
            self.run_kubectl(["create", "-f", "-"], input_text=doc)

    def apply_manifest_file_with_overrides(self, file_path: Path) -> None:
        self.apply_manifest_text_with_overrides(file_path.read_text(encoding="utf-8"))
