from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class RebuildManifestApplyConfig:
    root_dir: Path
    namespace: str
    profile: str
    include_optional: str
    enable_unpackerr: str
    kubectl: list[str]


class RebuildManifestApplyService:
    def __init__(
        self,
        cfg: RebuildManifestApplyConfig,
        *,
        info: Callable[[str], None],
        warn: Callable[[str], None],
        run_kubectl: Callable[..., subprocess.CompletedProcess[str]],
        apply_manifest_text_with_overrides: Callable[[str], None],
        apply_manifest_file_with_overrides: Callable[[Path], None],
        subprocess_run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.cfg = cfg
        self.info = info
        self.warn = warn
        self.run_kubectl = run_kubectl
        self.apply_manifest_text_with_overrides = apply_manifest_text_with_overrides
        self.apply_manifest_file_with_overrides = apply_manifest_file_with_overrides
        self.subprocess_run = subprocess_run

    def apply_manifests_for_profile(self) -> None:
        profile_dir = self.cfg.root_dir / "k8s" / "profiles" / self.cfg.profile
        build_failed = False

        if profile_dir.is_dir():
            self.info(
                f"Applying manifests for profile '{self.cfg.profile}' via {profile_dir} "
                "(namespace/path overrides enabled)"
            )
            proc = self.subprocess_run(
                [*self.cfg.kubectl, "kustomize", "--load-restrictor=LoadRestrictionsNone", str(profile_dir)],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode == 0:
                self.apply_manifest_text_with_overrides(proc.stdout)
                return
            message = (proc.stderr or proc.stdout or "").strip().splitlines()
            self.warn(
                "Profile kustomize build failed: "
                f"{message[-1] if message else 'unknown error'}"
            )
            build_failed = True

        if build_failed:
            self.warn("Profile kustomize build failed (possibly load restrictions or invalid profile resources).")
            self.warn(f"Falling back to direct manifest apply for profile '{self.cfg.profile}'.")
        else:
            self.warn(
                f"Profile directory not found for '{self.cfg.profile}'; falling back to direct manifest apply."
            )

        proc = self.subprocess_run(
            [*self.cfg.kubectl, "kustomize", "--load-restrictor=LoadRestrictionsNone", str(self.cfg.root_dir / "k8s")],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            self.apply_manifest_text_with_overrides(proc.stdout)
        else:
            message = (proc.stderr or proc.stdout or "").strip().splitlines()
            self.warn(f"Base kustomize build failed: {message[-1] if message else 'unknown error'}")
            ordered_files = [
                "namespace.yaml",
                "hardening.yaml",
                "secrets.example.yaml",
                "storage-pvc.yaml",
                "core.yaml",
                "ingress-traefik.yaml",
                "scale-policy.yaml",
            ]
            for name in ordered_files:
                self.apply_manifest_file_with_overrides(self.cfg.root_dir / "k8s" / name)

        if self.cfg.profile in {"full", "public-demo", "power-user"} or self.cfg.include_optional == "1":
            self.apply_manifest_file_with_overrides(self.cfg.root_dir / "k8s" / "optional.yaml")

        if self.cfg.profile in {"full", "power-user"} or self.cfg.enable_unpackerr == "1":
            self.apply_manifest_file_with_overrides(self.cfg.root_dir / "k8s" / "unpackerr.yaml")

        if self.cfg.profile == "public-demo":
            for app in ["qbittorrent", "sonarr", "radarr", "lidarr", "readarr", "bazarr", "sabnzbd"]:
                exists = self.subprocess_run(
                    [*self.cfg.kubectl, "-n", self.cfg.namespace, "get", "deploy", app],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if exists.returncode == 0:
                    self.info(f"public-demo profile: scaling deploy/{app} to 0")
                    self.run_kubectl(
                        ["-n", self.cfg.namespace, "scale", f"deploy/{app}", "--replicas=0"]
                    )
                else:
                    self.info(
                        f"public-demo profile: deploy/{app} not installed; "
                        "skipping scale-to-zero patch"
                    )

        if self.cfg.profile == "power-user":
            tls_patch = {
                "spec": {
                    "tls": [
                        {
                            "secretName": "media-stack-tls",
                            "hosts": [
                                "homepage.local",
                                "jellyfin.local",
                                "jellyseerr.local",
                                "sonarr.local",
                                "radarr.local",
                                "lidarr.local",
                                "readarr.local",
                                "bazarr.local",
                                "prowlarr.local",
                                "qbittorrent.local",
                                "sabnzbd.local",
                                "maintainerr.local",
                                "tautulli.local",
                            ],
                        }
                    ]
                }
            }
            self.info("power-user profile: applying TLS hosts patch to ingress/media-stack-ingress")
            self.run_kubectl(
                [
                    "-n",
                    self.cfg.namespace,
                    "patch",
                    "ingress",
                    "media-stack-ingress",
                    "--type",
                    "merge",
                    "-p",
                    json.dumps(tls_patch),
                ]
            )
