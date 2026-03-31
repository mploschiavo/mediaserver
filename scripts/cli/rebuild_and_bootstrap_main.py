#!/usr/bin/env python3
"""Python CLI for rebuild-and-bootstrap orchestration.

Media Automation Stack by Matthew Loschiavo:
https://matthewloschiavo.com
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib import request

from core.kube import resolve_kubectl_binary
from core.phase_tracker import PhaseTracker


def ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def info(message: str) -> None:
    print(f"[{ts()}] [INFO] {message}", flush=True)


def warn(message: str) -> None:
    print(f"[{ts()}] [WARN] {message}", file=sys.stderr, flush=True)


def err(message: str) -> None:
    print(f"[{ts()}] [ERR] {message}", file=sys.stderr, flush=True)


class RebuildError(RuntimeError):
    """Raised when rebuild/bootstrap orchestration fails."""


class SkipPhase(RuntimeError):
    """Signal that current phase should be marked as skipped."""


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


@dataclass
class RebuildBootstrapRunner:
    cfg: RebuildBootstrapConfig
    kubectl: list[str]
    tracker: PhaseTracker = field(default_factory=lambda: PhaseTracker(info=info, warn=warn))
    backup_secret_values: dict[str, str] = field(default_factory=dict)

    def run(self) -> int:
        self._validate_inputs()

        info("Starting full media-stack rebuild/bootstrap")
        self._run_phase("Resolve profile defaults", self.apply_profile_defaults)
        info(f"Namespace: {self.cfg.namespace}")
        info(f"Profile: {self.cfg.profile}")
        info(f"Ingress domain: {self.cfg.ingress_domain}")
        info(f"Config: {self.cfg.config_file}")
        info(f"Delete namespace: {self.cfg.delete_namespace}")
        info(f"Storage mode: {self.cfg.storage_mode}")
        if self.cfg.pvc_storage_class:
            info(f"PVC storage class override: {self.cfg.pvc_storage_class}")
        else:
            info("PVC storage class override: <cluster default>")
        info(f"Include optional: {self.cfg.include_optional}")
        info(f"Enable Unpackerr: {self.cfg.enable_unpackerr}")
        info(f"Run bootstrap: {self.cfg.run_bootstrap}")
        info(f"Generate secrets on rebuild: {self.cfg.generate_secrets_on_rebuild}")
        info(f"Preserve secret on rebuild: {self.cfg.preserve_secret_on_rebuild}")

        self.notify(
            "info",
            f"media-stack rebuild/bootstrap started (profile={self.cfg.profile}, namespace={self.cfg.namespace})",
        )

        self._run_phase(
            "Validate bootstrap config schema",
            lambda: self._run_script("validate-bootstrap-config.sh", "--config", str(self.cfg.config_file)),
        )

        if self.cfg.skip_prepare_host != "1":
            self._run_phase("Prepare host directories", self.prepare_host_directories)
        else:
            self._run_phase("Prepare host directories", lambda: None, enabled=False)

        self._run_phase("Backup existing credentials", self.backup_existing_secret_values)
        self._run_phase("Delete namespace (optional)", self.delete_namespace_optional)
        self._run_phase("Apply manifests for profile", self.apply_manifests_for_profile)

        if self.cfg.generate_secrets_on_rebuild == "1":
            self._run_phase("Generate secrets", self.generate_secrets)
        else:
            self._run_phase("Generate secrets", lambda: None, enabled=False)

        self._run_phase("Restore preserved credentials", self.restore_secret_values_from_backup)
        self._run_phase("Patch ingress class", self.patch_ingress_class)
        self._run_phase("Wait for deployments", self.wait_for_deployments)

        if self.cfg.run_bootstrap == "1":
            self._run_phase("Apply scale-policy guardrails", self.apply_scale_policy_guardrails)
            self._run_phase("Run bootstrap pipeline", self.run_bootstrap_pipeline)
        else:
            self._run_phase("Apply scale-policy guardrails", self.skip_scale_policy_guardrails, enabled=True)
            self._run_phase("Run bootstrap pipeline", self.skip_bootstrap_pipeline, enabled=True)

        if self.cfg.run_smoke_test == "1":
            self._run_phase("Run ingress smoke test", self.run_smoke_test)
        else:
            self._run_phase("Run ingress smoke test", lambda: None, enabled=False)

        self._run_phase("Collect final pod status", self.print_final_pod_status)
        self.tracker.summary()

        print("\n[OK] Rebuild + bootstrap completed.")
        self.notify(
            "ok",
            f"media-stack rebuild/bootstrap succeeded (profile={self.cfg.profile}, namespace={self.cfg.namespace})",
        )
        return 0

    def _run_phase(self, name: str, fn: Callable[[], None], *, enabled: bool = True) -> None:
        self.tracker.start(name)
        if not enabled:
            self.tracker.end("skipped")
            return
        try:
            fn()
            self.tracker.end("ok")
        except SkipPhase:
            self.tracker.end("skipped")
        except Exception:
            self.tracker.end("failed")
            raise

    def _validate_inputs(self) -> None:
        if not self.cfg.config_file.exists():
            raise RebuildError(f"Config file not found: {self.cfg.config_file}")
        if not self.cfg.namespace.strip():
            raise RebuildError("NAMESPACE cannot be empty.")
        self.cfg.ingress_domain = self.cfg.ingress_domain.lstrip(".").strip()
        if not self.cfg.ingress_domain:
            raise RebuildError("INGRESS_DOMAIN cannot be empty.")
        if self.cfg.storage_mode not in {"dynamic-pvc", "legacy-hostpath"}:
            raise RebuildError(
                f"Unsupported STORAGE_MODE '{self.cfg.storage_mode}'. Use dynamic-pvc|legacy-hostpath."
            )
        if self.cfg.profile not in {"minimal", "full", "public-demo", "power-user"}:
            raise RebuildError(
                f"Unknown PROFILE '{self.cfg.profile}'. Supported: minimal, full, public-demo, power-user."
            )

    def apply_profile_defaults(self) -> None:
        if self.cfg.profile == "minimal":
            self.cfg.include_optional = self.cfg.include_optional or "0"
            self.cfg.enable_unpackerr = self.cfg.enable_unpackerr or "0"
            self.cfg.run_bootstrap = self.cfg.run_bootstrap or "1"
            return
        if self.cfg.profile == "full":
            self.cfg.include_optional = self.cfg.include_optional or "1"
            self.cfg.enable_unpackerr = self.cfg.enable_unpackerr or "1"
            self.cfg.run_bootstrap = self.cfg.run_bootstrap or "1"
            return
        if self.cfg.profile == "public-demo":
            self.cfg.include_optional = self.cfg.include_optional or "1"
            self.cfg.enable_unpackerr = self.cfg.enable_unpackerr or "0"
            self.cfg.run_bootstrap = self.cfg.run_bootstrap or "0"
            return
        if self.cfg.profile == "power-user":
            self.cfg.include_optional = self.cfg.include_optional or "1"
            self.cfg.enable_unpackerr = self.cfg.enable_unpackerr or "1"
            self.cfg.run_bootstrap = self.cfg.run_bootstrap or "1"
            return
        raise RebuildError(f"Unsupported profile: {self.cfg.profile}")

    def _run_script(self, script_name: str, *args: str, env: dict[str, str] | None = None) -> None:
        script_path = self.cfg.root_dir / "scripts" / script_name
        merged_env = dict(os.environ)
        merged_env.update({"NAMESPACE": self.cfg.namespace})
        if env:
            merged_env.update({k: str(v) for k, v in env.items()})

        proc = subprocess.run(
            ["bash", str(script_path), *args],
            cwd=str(self.cfg.root_dir),
            env=merged_env,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.stdout.strip():
            print(proc.stdout.rstrip())
        if proc.stderr.strip():
            print(proc.stderr.rstrip(), file=sys.stderr)
        if proc.returncode != 0:
            raise RebuildError(
                f"{script_name} failed ({proc.returncode}): "
                f"{' '.join(shlex.quote(x) for x in [str(script_path), *args])}"
            )

    def _run_kubectl(
        self,
        args: list[str],
        *,
        check: bool = True,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(
            [*self.kubectl, *args],
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.stdout.strip():
            print(proc.stdout.rstrip())
        if proc.stderr.strip():
            print(proc.stderr.rstrip(), file=sys.stderr)
        if check and proc.returncode != 0:
            raise RebuildError(
                f"kubectl command failed ({proc.returncode}): "
                f"{' '.join(shlex.quote(x) for x in [*self.kubectl, *args])}"
            )
        return proc

    def notify(self, status: str, message: str) -> None:
        if not self.cfg.alert_webhook_url:
            return
        payload = json.dumps({"status": status, "message": message}).encode("utf-8")
        req = request.Request(
            self.cfg.alert_webhook_url,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with request.urlopen(req, timeout=8):
                return
        except Exception:
            return

    def prepare_host_directories(self) -> None:
        if self.cfg.storage_mode == "legacy-hostpath":
            info(f"Preparing host directories under {self.cfg.prepare_host_root}")
            self._run_script("prepare-host.sh", self.cfg.prepare_host_root)
            return
        info(f"Skipping host directory prep (storage mode: {self.cfg.storage_mode})")
        raise SkipPhase()

    def backup_existing_secret_values(self) -> None:
        if self.cfg.preserve_secret_on_rebuild != "1":
            info("Secret preservation disabled (PRESERVE_SECRET_ON_REBUILD=0).")
            return

        proc = subprocess.run(
            [*self.kubectl, "-n", self.cfg.namespace, "get", "secret", self.cfg.secret_name, "-o", "json"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            info(f"No existing secret {self.cfg.namespace}/{self.cfg.secret_name} found to preserve.")
            return

        payload = json.loads(proc.stdout or "{}")
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            data = {}

        keys = [
            "QBITTORRENT_USERNAME",
            "QBITTORRENT_PASSWORD",
            "SABNZBD_API_KEY",
            "STACK_ADMIN_USERNAME",
            "STACK_ADMIN_PASSWORD",
            "JELLYFIN_API_KEY",
            "JELLYFIN_USER_ID",
            "UNPACKERR_SONARR_API_KEY",
            "UNPACKERR_RADARR_API_KEY",
            "UNPACKERR_LIDARR_API_KEY",
            "UNPACKERR_READARR_API_KEY",
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

        self.backup_secret_values = restored
        if restored:
            info(
                f"Backed up {len(restored)} secret key(s) from "
                f"{self.cfg.namespace}/{self.cfg.secret_name}."
            )
        else:
            info(
                f"Secret {self.cfg.namespace}/{self.cfg.secret_name} exists but has no matching keys to preserve."
            )

    def restore_secret_values_from_backup(self) -> None:
        if not self.backup_secret_values:
            info("No preserved secret values to restore.")
            return

        exists = subprocess.run(
            [*self.kubectl, "-n", self.cfg.namespace, "get", "secret", self.cfg.secret_name],
            capture_output=True,
            text=True,
            check=False,
        )
        if exists.returncode != 0:
            info(
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
            self._run_kubectl(["apply", "-f", "-"], input_text=manifest)

        patch_payload = json.dumps({"stringData": self.backup_secret_values})
        self._run_kubectl(
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
        info(f"Restored preserved values into {self.cfg.namespace}/{self.cfg.secret_name}.")

    def delete_namespace_optional(self) -> None:
        if self.cfg.delete_namespace != "1":
            raise SkipPhase()

        exists = subprocess.run(
            [*self.kubectl, "get", "namespace", self.cfg.namespace],
            capture_output=True,
            text=True,
            check=False,
        )
        if exists.returncode != 0:
            info(f"Namespace/{self.cfg.namespace} does not exist; continuing")
            return

        info(f"Deleting namespace/{self.cfg.namespace}")
        self._run_kubectl(["delete", "namespace", self.cfg.namespace, "--wait=false"])
        self.wait_for_namespace_deleted()

    def wait_for_namespace_deleted(self, max_wait_seconds: int = 600) -> None:
        waited = 0
        while True:
            probe = subprocess.run(
                [*self.kubectl, "get", "namespace", self.cfg.namespace],
                capture_output=True,
                text=True,
                check=False,
            )
            if probe.returncode != 0:
                return
            if waited >= max_wait_seconds:
                raise RebuildError(
                    f"Namespace '{self.cfg.namespace}' is still terminating after {max_wait_seconds}s."
                )
            info(f"Waiting for namespace/{self.cfg.namespace} deletion (elapsed {waited}s)")
            time.sleep(5)
            waited += 5

    def _stream_with_manifest_overrides(self, text: str) -> str:
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
        return out

    def _inject_storage_class(self, text: str) -> str:
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

    def _apply_manifest_text_with_overrides(self, text: str) -> None:
        patched = self._inject_storage_class(self._stream_with_manifest_overrides(text))
        self._run_kubectl(["apply", "-f", "-"], input_text=patched)

    def _apply_manifest_file_with_overrides(self, file_path: Path) -> None:
        self._apply_manifest_text_with_overrides(file_path.read_text(encoding="utf-8"))

    def apply_manifests_for_profile(self) -> None:
        profile_dir = self.cfg.root_dir / "k8s" / "profiles" / self.cfg.profile
        build_failed = False

        if profile_dir.is_dir():
            info(
                f"Applying manifests for profile '{self.cfg.profile}' via {profile_dir} "
                "(namespace/path overrides enabled)"
            )
            proc = subprocess.run(
                [*self.kubectl, "kustomize", "--load-restrictor=LoadRestrictionsNone", str(profile_dir)],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode == 0:
                self._apply_manifest_text_with_overrides(proc.stdout)
                return
            message = (proc.stderr or proc.stdout or "").strip().splitlines()
            warn(
                "Profile kustomize build failed: "
                f"{message[-1] if message else 'unknown error'}"
            )
            build_failed = True

        if build_failed:
            warn("Profile kustomize build failed (possibly load restrictions or invalid profile resources).")
            warn(f"Falling back to direct manifest apply for profile '{self.cfg.profile}'.")
        else:
            warn(f"Profile directory not found for '{self.cfg.profile}'; falling back to direct manifest apply.")

        proc = subprocess.run(
            [*self.kubectl, "kustomize", "--load-restrictor=LoadRestrictionsNone", str(self.cfg.root_dir / "k8s")],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            self._apply_manifest_text_with_overrides(proc.stdout)
        else:
            message = (proc.stderr or proc.stdout or "").strip().splitlines()
            warn(f"Base kustomize build failed: {message[-1] if message else 'unknown error'}")
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
                self._apply_manifest_file_with_overrides(self.cfg.root_dir / "k8s" / name)

        if self.cfg.profile in {"full", "public-demo", "power-user"} or self.cfg.include_optional == "1":
            self._apply_manifest_file_with_overrides(self.cfg.root_dir / "k8s" / "optional.yaml")

        if self.cfg.profile in {"full", "power-user"} or self.cfg.enable_unpackerr == "1":
            self._apply_manifest_file_with_overrides(self.cfg.root_dir / "k8s" / "unpackerr.yaml")

        if self.cfg.profile == "public-demo":
            for app in ["qbittorrent", "sonarr", "radarr", "lidarr", "readarr", "bazarr", "sabnzbd"]:
                exists = subprocess.run(
                    [*self.kubectl, "-n", self.cfg.namespace, "get", "deploy", app],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if exists.returncode == 0:
                    info(f"public-demo profile: scaling deploy/{app} to 0")
                    self._run_kubectl(
                        ["-n", self.cfg.namespace, "scale", f"deploy/{app}", "--replicas=0"]
                    )
                else:
                    info(
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
            info("power-user profile: applying TLS hosts patch to ingress/media-stack-ingress")
            self._run_kubectl(
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

    def generate_secrets(self) -> None:
        info("Generating secure secrets in cluster before bootstrap")
        self._run_script(
            "generate-secrets.sh",
            env={
                "NAMESPACE": self.cfg.namespace,
                "OUTPUT_FILE": str(self.cfg.root_dir / "secrets.generated.env"),
            },
        )

    def pick_ingress_class(self) -> str:
        if self.cfg.ingress_class != "auto":
            return self.cfg.ingress_class

        proc = subprocess.run(
            [
                *self.kubectl,
                "get",
                "ingressclass",
                "-o",
                "jsonpath={range .items[*]}{.metadata.name}{'\\n'}{end}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        classes = [x.strip() for x in (proc.stdout or "").splitlines() if x.strip()]
        for target in ("public", "nginx"):
            if target in classes:
                return target
        return classes[0] if classes else ""

    def patch_ingress_class(self) -> None:
        desired_class = self.pick_ingress_class()
        if not desired_class:
            warn("No ingress classes discovered; skipping ingress patch.")
            raise SkipPhase()

        current = subprocess.run(
            [
                *self.kubectl,
                "-n",
                self.cfg.namespace,
                "get",
                "ingress",
                "media-stack-ingress",
                "-o",
                "jsonpath={.spec.ingressClassName}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        current_class = (current.stdout or "").strip()
        if current_class == desired_class:
            info(f"Ingress class already set to '{desired_class}'")
            return

        info(
            f"Patching ingress class to '{desired_class}' "
            f"(current: '{current_class if current_class else '<empty>'}')"
        )
        self._run_script(
            "microk8s-patch-ingress-class.sh",
            desired_class,
            env={"NAMESPACE": self.cfg.namespace},
        )

    def wait_for_deployments(self) -> None:
        proc = subprocess.run(
            [
                *self.kubectl,
                "-n",
                self.cfg.namespace,
                "get",
                "deploy",
                "-o",
                "jsonpath={range .items[*]}{.metadata.name}{'\\n'}{end}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RebuildError("Failed listing deployments.")

        deploys = [x.strip() for x in (proc.stdout or "").splitlines() if x.strip()]
        if not deploys:
            raise RebuildError(f"No deployments found in namespace '{self.cfg.namespace}'.")

        failures = 0
        for deploy in deploys:
            replica_probe = subprocess.run(
                [
                    *self.kubectl,
                    "-n",
                    self.cfg.namespace,
                    "get",
                    "deploy",
                    deploy,
                    "-o",
                    "jsonpath={.spec.replicas}",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            replicas = (replica_probe.stdout or "1").strip() or "1"
            if replicas == "0":
                info(f"Skipping rollout wait for deploy/{deploy} (replicas=0)")
                continue

            info(f"Waiting for deploy/{deploy} rollout")
            rollout = subprocess.run(
                [
                    *self.kubectl,
                    "-n",
                    self.cfg.namespace,
                    "rollout",
                    "status",
                    f"deploy/{deploy}",
                    f"--timeout={self.cfg.wait_timeout}",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if rollout.stdout.strip():
                print(rollout.stdout.rstrip())
            if rollout.stderr.strip():
                print(rollout.stderr.rstrip(), file=sys.stderr)
            if rollout.returncode != 0:
                warn(f"deploy/{deploy} not ready within {self.cfg.wait_timeout}")
                failures += 1

        if failures:
            subprocess.run(
                [*self.kubectl, "-n", self.cfg.namespace, "get", "pods", "-o", "wide"],
                check=False,
            )
            raise RebuildError(f"{failures} deployment(s) failed readiness checks.")

    def apply_scale_policy_guardrails(self) -> None:
        info("Applying scale-policy guardrails")
        self._run_script("apply-scale-policy.sh", env={"NAMESPACE": self.cfg.namespace})

    def skip_scale_policy_guardrails(self) -> None:
        info("Scale-policy guardrails skipped for non-bootstrap profile.")
        raise SkipPhase()

    def run_bootstrap_pipeline(self) -> None:
        info("Running full bootstrap pipeline")
        self._run_script(
            "bootstrap-all.sh",
            str(self.cfg.config_file),
            env={
                "NAMESPACE": self.cfg.namespace,
                "PREPARE_HOST_ROOT": self.cfg.prepare_host_root,
                "ENABLE_UNPACKERR": self.cfg.enable_unpackerr,
            },
        )

    def skip_bootstrap_pipeline(self) -> None:
        info("Bootstrap skipped by profile/policy.")
        raise SkipPhase()

    def run_smoke_test(self) -> None:
        if not self.cfg.node_ip:
            probe = subprocess.run(
                ["bash", "-lc", "hostname -I | awk '{print $1}'"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.cfg.node_ip = (probe.stdout or "").strip()

        if not self.cfg.node_ip:
            warn("Could not detect NODE_IP; skipping smoke test.")
            raise SkipPhase()

        info(f"Running ingress smoke test against node IP {self.cfg.node_ip}")
        self._run_script(
            "microk8s-smoke-test.sh",
            self.cfg.node_ip,
            env={"NAMESPACE": self.cfg.namespace},
        )

    def print_final_pod_status(self) -> None:
        info("Final pod status:")
        self._run_kubectl(["-n", self.cfg.namespace, "get", "pods"])



def parse_args(argv: list[str]) -> RebuildBootstrapConfig:
    root_dir = Path(__file__).resolve().parents[2]

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


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    cfg = parse_args(args)

    try:
        kubectl = resolve_kubectl_binary()
    except Exception as exc:
        err(str(exc))
        return 2

    runner = RebuildBootstrapRunner(cfg=cfg, kubectl=kubectl)
    try:
        return runner.run()
    except Exception as exc:
        warn(f"Rebuild/bootstrap failed: {exc}")
        warn("Pod status snapshot at failure:")
        subprocess.run([*kubectl, "-n", cfg.namespace, "get", "pods", "-o", "wide"], check=False)
        runner.tracker.summary()
        runner.notify(
            "error",
            f"media-stack rebuild/bootstrap failed (profile={cfg.profile}, namespace={cfg.namespace})",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
