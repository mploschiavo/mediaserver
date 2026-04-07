"""Manifest/PVC/configmap helpers for bootstrap job orchestration."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable

from media_stack.core.exceptions import ConfigError, KubernetesError
from media_stack.core.platforms.kubernetes.kube_client import KubernetesClient

LogFn = Callable[[str], None]


@dataclass(frozen=True)
class ControllerManifestConfig:
    namespace: str
    root_dir: Path
    prepare_host_root: str
    bootstrap_runner_image: str
    job_config_file: Path
    bootstrap_profile_file: str = ""


@dataclass
class ControllerManifestService:
    cfg: ControllerManifestConfig
    kube: KubernetesClient
    info: LogFn
    warn: LogFn

    def manifest_overrides(self, text: str) -> str:
        out = re.sub(
            r"namespace:\s*media-stack\b",
            f"namespace: {self.cfg.namespace}",
            text,
        )
        out = re.sub(
            r"name:\s*media-stack\s*$",
            f"name: {self.cfg.namespace}",
            out,
            flags=re.MULTILINE,
        )
        out = re.sub(
            r"image:\s*192\.168\.1\.60:30002/library/media-stack-controller:latest",
            f"image: {self.cfg.bootstrap_runner_image}",
            out,
        )
        out = out.replace("/srv/media-stack", self.cfg.prepare_host_root)
        return out

    @staticmethod
    def _leading_spaces(line: str) -> int:
        return len(line) - len(line.lstrip(" "))

    @classmethod
    def _extract_pvc_names_from_manifest(cls, manifest_text: str) -> list[str]:
        names: list[str] = []
        docs = re.split(r"(?m)^---\s*$", str(manifest_text or ""))
        for doc in docs:
            if not re.search(r"(?m)^\s*kind:\s*PersistentVolumeClaim\s*$", doc):
                continue

            in_metadata = False
            metadata_indent = 0
            name = ""
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
                    break

            if name and name not in names:
                names.append(name)
        return names

    def ensure_bootstrap_pvc_prereqs(self) -> None:
        storage_manifest = self.cfg.root_dir / "k8s" / "storage-pvc.yaml"
        if not storage_manifest.exists():
            raise ConfigError(
                f"PVC manifest required for bootstrap prerequisites: {storage_manifest}"
            )

        self.info(f"Ensuring bootstrap PVC prerequisites via {storage_manifest}")
        with TemporaryDirectory(prefix="media-stack-storage-pvc-") as tmpdir:
            patched = Path(tmpdir) / "storage-pvc.yaml"
            patched_text = self.manifest_overrides(storage_manifest.read_text(encoding="utf-8"))
            required = self._extract_pvc_names_from_manifest(patched_text)
            if not required:
                raise ConfigError(
                    "storage-pvc.yaml did not declare any PersistentVolumeClaims; "
                    "bootstrap cannot continue."
                )
            patched.write_text(
                patched_text,
                encoding="utf-8",
            )
            result = self.kube.run(["apply", "-f", str(patched)], check=False)
            if result.stdout.strip():
                print(result.stdout.rstrip())
            if result.stderr.strip():
                print(result.stderr.rstrip(), file=sys.stderr)

        missing = []
        for pvc in required:
            result = self.kube.run(
                ["-n", self.cfg.namespace, "get", "pvc", pvc],
                check=False,
            )
            if result.returncode != 0:
                missing.append(pvc)

        if missing:
            self.warn(f"Missing required PVC(s) for bootstrap service: {' '.join(missing)}")
            self.warn(
                "Apply storage PVCs and retry: "
                f"{' '.join(self.kube.cmd_prefix)} apply -f {self.cfg.root_dir / 'k8s' / 'storage-pvc.yaml'}"
            )
            raise ConfigError("Missing required PVCs for bootstrap service")

        self.info("Bootstrap PVC prerequisites are present.")

    def _replace_or_create_yaml(self, yaml_path: Path, kind_name: str) -> None:
        replaced = self.kube.run(
            ["-n", self.cfg.namespace, "replace", "-f", str(yaml_path)],
            check=False,
        )
        if replaced.returncode == 0:
            self.info(f"{kind_name} replaced")
            return
        created = self.kube.run(
            ["-n", self.cfg.namespace, "create", "-f", str(yaml_path)],
            check=False,
        )
        if created.returncode != 0:
            raise KubernetesError(created.stderr or created.stdout)

    def update_bootstrap_configmaps(self) -> None:
        self.info("Updating bootstrap config ConfigMap")
        with TemporaryDirectory(prefix="media-stack-controller-config-") as tmpdir:
            configmap_yaml = Path(tmpdir) / "bootstrap-config.yaml"
            generated = self.kube.run(
                [
                    "-n",
                    self.cfg.namespace,
                    "create",
                    "configmap",
                    "media-stack-controller-config",
                    f"--from-file=config.json={self.cfg.job_config_file}",
                    "--dry-run=client",
                    "-o",
                    "yaml",
                ]
            )
            configmap_yaml.write_text(generated.stdout, encoding="utf-8")
            self._replace_or_create_yaml(configmap_yaml, "configmap/media-stack-controller-config")

        profile_path = str(self.cfg.bootstrap_profile_file or "").strip()
        if profile_path and Path(profile_path).is_file():
            self.info("Updating bootstrap profile ConfigMap")
            with TemporaryDirectory(prefix="media-stack-controller-profile-") as tmpdir:
                profile_yaml = Path(tmpdir) / "bootstrap-profile.yaml"
                generated = self.kube.run(
                    [
                        "-n",
                        self.cfg.namespace,
                        "create",
                        "configmap",
                        "media-stack-controller-profile",
                        f"--from-file=profile.yaml={profile_path}",
                        "--dry-run=client",
                        "-o",
                        "yaml",
                    ]
                )
                profile_yaml.write_text(generated.stdout, encoding="utf-8")
                self._replace_or_create_yaml(
                    profile_yaml, "configmap/media-stack-controller-profile"
                )
        else:
            self.warn(
                "No bootstrap profile file available — "
                "skipping media-stack-controller-profile ConfigMap"
            )

    def ensure_bootstrap_deployment(self) -> None:
        """Ensure bootstrap Deployment is running and restart it to pick up new ConfigMaps."""
        self.info("Ensuring bootstrap Deployment is ready with updated ConfigMaps")
        # The Deployment is created by the kustomization (k8s/profiles/standard/).
        # Restart it to pick up the updated ConfigMaps we just created.
        result = self.kube.run(
            ["-n", self.cfg.namespace, "rollout", "restart", "deploy/media-stack-controller"],
            check=False,
        )
        if result.returncode != 0:
            self.warn(f"Bootstrap Deployment restart: {result.stderr or result.stdout}")
        # Wait for the rollout to complete.
        self.kube.run(
            ["-n", self.cfg.namespace, "rollout", "status", "deploy/media-stack-controller",
             "--timeout=120s"],
            check=False,
        )
        self.info("Bootstrap Deployment ready")

    # Backward-compatible alias.
    recreate_bootstrap_job = ensure_bootstrap_deployment
