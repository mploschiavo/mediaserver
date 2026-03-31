"""Manifest/PVC/configmap helpers for bootstrap job orchestration."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable

from core.exceptions import ConfigError, KubernetesError
from core.kube import KubectlClient

LogFn = Callable[[str], None]


@dataclass(frozen=True)
class BootstrapManifestConfig:
    namespace: str
    root_dir: Path
    prepare_host_root: str
    bootstrap_runner_image: str
    job_config_file: Path


@dataclass
class BootstrapManifestService:
    cfg: BootstrapManifestConfig
    kube: KubectlClient
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
            r"image:\s*192\.168\.1\.60:30002/library/media-stack-bootstrap-runner:latest",
            f"image: {self.cfg.bootstrap_runner_image}",
            out,
        )
        out = out.replace("/srv/media-stack", self.cfg.prepare_host_root)
        return out

    def ensure_bootstrap_pvc_prereqs(self) -> None:
        storage_manifest = self.cfg.root_dir / "k8s" / "storage-pvc.yaml"
        required = [
            "media-stack-config-jellyfin",
            "media-stack-config-jellyseerr",
            "media-stack-config-sonarr",
            "media-stack-config-radarr",
            "media-stack-config-lidarr",
            "media-stack-config-readarr",
            "media-stack-config-bazarr",
            "media-stack-config-prowlarr",
            "media-stack-config-sabnzbd",
            "media-stack-config-homepage",
            "media-stack-config-maintainerr",
            "media-stack-config-jellyfin-auto-collections",
            "media-stack-data-torrents",
            "media-stack-data-usenet",
            "media-stack-media",
        ]

        if storage_manifest.exists():
            self.info(f"Ensuring bootstrap PVC prerequisites via {storage_manifest}")
            with TemporaryDirectory(prefix="media-stack-storage-pvc-") as tmpdir:
                patched = Path(tmpdir) / "storage-pvc.yaml"
                patched.write_text(
                    self.manifest_overrides(storage_manifest.read_text(encoding="utf-8")),
                    encoding="utf-8",
                )
                result = self.kube.run(["apply", "-f", str(patched)], check=False)
                if result.stdout.strip():
                    print(result.stdout.rstrip())
                if result.stderr.strip():
                    print(result.stderr.rstrip(), file=sys.stderr)
        else:
            self.warn(f"PVC manifest not found at {storage_manifest}")

        missing = []
        for pvc in required:
            result = self.kube.run(
                ["-n", self.cfg.namespace, "get", "pvc", pvc],
                check=False,
            )
            if result.returncode != 0:
                missing.append(pvc)

        if missing:
            self.warn(f"Missing required PVC(s) for bootstrap job: {' '.join(missing)}")
            self.warn(
                "Apply storage PVCs and retry: "
                f"{' '.join(self.kube.cmd_prefix)} apply -f {self.cfg.root_dir / 'k8s' / 'storage-pvc.yaml'}"
            )
            raise ConfigError("Missing required PVCs for bootstrap job")

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
        with TemporaryDirectory(prefix="media-stack-bootstrap-config-") as tmpdir:
            configmap_yaml = Path(tmpdir) / "bootstrap-config.yaml"
            generated = self.kube.run(
                [
                    "-n",
                    self.cfg.namespace,
                    "create",
                    "configmap",
                    "media-stack-bootstrap-config",
                    f"--from-file=config.json={self.cfg.job_config_file}",
                    "--dry-run=client",
                    "-o",
                    "yaml",
                ]
            )
            configmap_yaml.write_text(generated.stdout, encoding="utf-8")
            self._replace_or_create_yaml(configmap_yaml, "configmap/media-stack-bootstrap-config")

    def recreate_bootstrap_job(self) -> None:
        self.info("Recreating bootstrap Job")
        self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "delete",
                "job",
                "media-stack-bootstrap",
                "--ignore-not-found",
            ],
            check=False,
        )
        manifest_path = self.cfg.root_dir / "k8s" / "bootstrap-job.yaml"
        with TemporaryDirectory(prefix="media-stack-bootstrap-job-") as tmpdir:
            patched = Path(tmpdir) / "bootstrap-job.yaml"
            patched.write_text(
                self.manifest_overrides(manifest_path.read_text(encoding="utf-8")),
                encoding="utf-8",
            )
            result = self.kube.run(
                ["-n", self.cfg.namespace, "apply", "-f", str(patched)],
                check=False,
            )
            if result.stdout.strip():
                print(result.stdout.rstrip())
            if result.stderr.strip():
                print(result.stderr.rstrip(), file=sys.stderr)
            if result.returncode != 0:
                raise KubernetesError(result.stderr or result.stdout)
