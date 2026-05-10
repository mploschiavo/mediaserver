"""Jellyfin GPU detection and hardware-transcoding auto-configuration.

Moved from ``api/services/ops.py`` to keep Jellyfin-specific system.xml
parsing and container inspection in the app layer.
"""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import os
import re
import time
from pathlib import Path
from typing import Any
import logging


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class JellyfinGpu:

    def check_jellyfin_gpu(self, docker_client: Any) -> dict[str, Any]:
        """Check whether the Jellyfin container has GPU passthrough.

        Inspects the running workload — compose path reads docker-py
        ``HostConfig`` (devices + runtime); k8s path reads the
        Deployment's ``resources.limits`` for ``nvidia.com/gpu`` and
        the pod spec's ``runtimeClassName``. Returns a dict with
        ``jellyfin_has_gpu`` (bool), ``jellyfin_platform`` (compose|
        k8s|none), and any ``jellyfin_configured`` / ``jellyfin_hw_type``
        keys derived from the Jellyfin ``system.xml`` on disk.
        """
        result: dict[str, Any] = {
            "jellyfin_has_gpu": False,
            "jellyfin_configured": False,
            "jellyfin_platform": "none",
        }

        # Compose path: inspect the docker container if reachable.
        try:
            jf = docker_client.containers.get("jellyfin")
            result["jellyfin_platform"] = "compose"
            devices = jf.attrs.get("HostConfig", {}).get("Devices") or []
            if any("/dev/dri" in str(d) for d in devices):
                result["jellyfin_has_gpu"] = True
            elif any("dri" in str(d) for d in devices):
                result["jellyfin_has_gpu"] = True
            # Check runtime for nvidia
            runtime = jf.attrs.get("HostConfig", {}).get("Runtime", "")
            if runtime == "nvidia":
                result["jellyfin_has_gpu"] = True
        except Exception as exc:
            log_swallowed(exc)

        # K8s path: if compose detection didn't find a container,
        # fall through to ``kubectl``-driven probe of the Jellyfin
        # Deployment in the env-configured namespace.
        if result["jellyfin_platform"] == "none":
            k8s_view = self._check_k8s_gpu()
            if k8s_view is not None:
                result["jellyfin_platform"] = "k8s"
                if k8s_view.get("has_gpu"):
                    result["jellyfin_has_gpu"] = True
                # Surface the resource detail so operators / job
                # evidence dicts can see which signal won.
                result["jellyfin_k8s_evidence"] = k8s_view.get("evidence", {})

        config_root = os.environ.get("CONFIG_ROOT", "/srv-config")
        jf_system = Path(config_root) / "jellyfin" / "config" / "system.xml"
        if jf_system.is_file():
            try:
                text = jf_system.read_text(encoding="utf-8")
                result["jellyfin_configured"] = (
                    "EnableHardwareDecoding" in text and ">true<" in text.lower()
                )
                m = re.search(
                    r"<HardwareAccelerationType>(\w+)</HardwareAccelerationType>", text
                )
                if m:
                    result["jellyfin_hw_type"] = m.group(1)
            except Exception as exc:
                log_swallowed(exc)

        return result

    def _check_k8s_gpu(self) -> dict[str, Any] | None:
        """Probe the jellyfin Deployment in k8s for GPU resources.

        Uses ``kubectl get deploy/jellyfin -o json`` against the
        env-configured ``K8S_NAMESPACE`` (defaults to ``media-stack``).
        Returns ``{has_gpu: bool, evidence: {...}}`` on success, or
        ``None`` when k8s isn't reachable / kubectl isn't available
        (so the caller treats k8s as "not the active platform" and
        leaves ``jellyfin_platform=none``).

        ``has_gpu`` is True when the Deployment carries the GPU
        operator's signature: ``nvidia.com/gpu`` in resource limits
        OR ``runtimeClassName: nvidia`` on the pod spec. Both are
        independently sufficient — the overlay sets both, but
        operators applying a custom patch may set only one.
        """
        import json
        import shutil
        import subprocess
        try:
            from media_stack.core.cli_common import kube_cmd
            tokens = list(kube_cmd())
        except Exception:  # noqa: BLE001
            return None
        if not tokens or not shutil.which(tokens[0]):
            return None
        namespace = (os.environ.get("K8S_NAMESPACE", "") or "media-stack").strip()
        try:
            proc = subprocess.run(
                [*tokens, "-n", namespace, "get", "deploy/jellyfin",
                 "-o", "json"],
                check=False, capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:
            return None
        try:
            spec = json.loads(proc.stdout)
        except (ValueError, TypeError):
            return None
        evidence: dict[str, Any] = {"namespace": namespace}
        has_gpu = False
        try:
            pod_spec = (
                spec.get("spec", {}).get("template", {}).get("spec", {})
            )
            runtime_class = pod_spec.get("runtimeClassName") or ""
            evidence["runtime_class"] = runtime_class
            if str(runtime_class).lower() == "nvidia":
                has_gpu = True
            for container in pod_spec.get("containers", []):
                limits = (container.get("resources") or {}).get("limits") or {}
                # ``nvidia.com/gpu`` is the canonical resource name
                # advertised by the GPU operator's device-plugin.
                # ``amd.com/gpu`` is the AMD ROCm equivalent — we
                # surface but don't auto-enable nvenc for AMD.
                gpu_resource = (
                    limits.get("nvidia.com/gpu")
                    or limits.get("amd.com/gpu")
                )
                if gpu_resource:
                    has_gpu = True
                    evidence["gpu_resource"] = str(gpu_resource)
                    evidence["gpu_vendor"] = (
                        "nvidia"
                        if limits.get("nvidia.com/gpu")
                        else "amd"
                    )
        except (TypeError, KeyError):
            pass
        evidence["has_gpu"] = has_gpu
        return {"has_gpu": has_gpu, "evidence": evidence}

    def build_compose_snippet(self, hw_accel_type: str) -> str:
        """Return a docker-compose snippet for the detected GPU type."""
        if hw_accel_type == "vaapi":
            return (
                "# Add to jellyfin service in docker-compose.yml:\n"
                "    devices:\n      - /dev/dri:/dev/dri\n"
                "    group_add:\n      - \"44\"   # video\n      - \"109\"  # render"
            )
        # nvidia
        return (
            "# Use the jellyfin-nvidia profile:\n# docker compose --profile nvidia up -d\n"
            "# Or add to jellyfin service:\n    runtime: nvidia\n    environment:\n"
            "      - NVIDIA_VISIBLE_DEVICES=all\n      - NVIDIA_DRIVER_CAPABILITIES=compute,video,utility"
        )

    def build_k8s_snippet(self, hw_accel_type: str) -> str:
        """Return a kubectl snippet that points at the right overlay."""
        if hw_accel_type == "vaapi":
            return (
                "# Apply the intel-vaapi overlay (Phase 5 of GPU support — not yet in repo):\n"
                "# kubectl apply -k deploy/k8s/overlays/intel-vaapi/"
            )
        # nvidia
        return (
            "# Apply the nvidia overlay on top of your profile:\n"
            "# kubectl apply -k deploy/k8s/profiles/<profile>\n"
            "# kubectl apply -k deploy/k8s/overlays/nvidia\n"
            "# Requires the NVIDIA GPU operator installed in the cluster\n"
            "# (nvidia-device-plugin-daemonset + RuntimeClass `nvidia`)."
        )

    def enable_gpu_transcoding(self) -> dict[str, Any]:
        """Auto-configure Jellyfin for hardware transcoding based on detected GPU.

        Creates a backup of system.xml before modifying. If Jellyfin fails to
        restart, automatically rolls back to the backup.

        This function is imported by ``api/services/ops.py`` and exposed as part
        of the ops service API.  It calls :func:`get_gpu_info` (in ops.py) to
        detect available GPUs before proceeding.
        """
        # Import lazily to avoid circular dependency (ops imports from us).
        from media_stack.api.services.ops import get_gpu_info

        gpu_info = get_gpu_info()

        if not gpu_info.get("detected"):
            return {"status": "error", "error": "No GPU detected. Mount GPU device to container first."}

        if not gpu_info.get("jellyfin_has_gpu"):
            return {"status": "error", "error": "Jellyfin container does not have GPU devices mounted.",
                    "compose_snippet": gpu_info.get("compose_snippet", "")}

        hw_type = gpu_info.get("hw_accel_type", "vaapi")
        config_root = Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
        system_xml = config_root / "jellyfin" / "config" / "system.xml"

        if not system_xml.is_file():
            return {"status": "error", "error": "Jellyfin system.xml not found. Start Jellyfin first."}

        original_text = system_xml.read_text(encoding="utf-8")

        if "</ServerConfiguration>" not in original_text:
            return {"status": "error", "error": "system.xml does not contain expected XML structure."}

        # Backup before modifying
        backup_path = system_xml.with_suffix(".xml.gpu-backup")
        try:
            backup_path.write_text(original_text, encoding="utf-8")
        except Exception as exc:
            return {"status": "error", "error": f"Failed to create backup: {exc}"}

        text = original_text
        changes: list[str] = []

        def _set_xml(content: str, tag: str, value: str) -> str:
            pattern = rf"<{tag}>.*?</{tag}>"
            replacement = f"<{tag}>{value}</{tag}>"
            if re.search(pattern, content):
                return re.sub(pattern, replacement, content)
            return content.replace("</ServerConfiguration>",
                                   f"  {replacement}\n</ServerConfiguration>")

        text = _set_xml(text, "HardwareAccelerationType", hw_type)
        changes.append(f"HardwareAccelerationType={hw_type}")

        for tag in ("EnableHardwareDecoding", "EnableHardwareEncoding"):
            text = _set_xml(text, tag, "true")
            changes.append(f"{tag}=true")

        if hw_type == "vaapi":
            text = _set_xml(text, "VaapiDevice", "/dev/dri/renderD128")
            changes.append("VaapiDevice=/dev/dri/renderD128")
            for codec in ("EnableTonemapping", "EnableVppTonemapping"):
                text = _set_xml(text, codec, "true")
                changes.append(f"{codec}=true")

        try:
            system_xml.write_text(text, encoding="utf-8")
        except Exception as exc:
            return {"status": "error", "error": f"Failed to write system.xml: {exc}"}

        # Restart Jellyfin via the right platform path. The
        # detection in ``gpu_info["jellyfin_platform"]`` (compose / k8s
        # / none) decides which restart mechanism to use; both
        # mechanisms re-probe afterwards so the rollback path is
        # symmetric.
        platform = str(gpu_info.get("jellyfin_platform") or "compose")
        restart_note, rollback = self._restart_jellyfin_for_platform(platform)

        if rollback:
            # Roll back to backup, then restart again on the same
            # platform so the rolled-back config takes effect.
            try:
                system_xml.write_text(
                    backup_path.read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
                self._restart_jellyfin_for_platform(platform)
                restart_note += " Rolled back to previous config and restarted."
            except Exception:
                restart_note += " Rollback written but manual restart needed."
            return {
                "status": "error",
                "error": "Jellyfin crashed after enabling GPU transcoding. Config rolled back.",
                "hw_accel_type": hw_type,
                "changes": changes,
                "note": restart_note,
                "backup": str(backup_path),
            }

        return {
            "status": "ok",
            "hw_accel_type": hw_type,
            "changes": changes,
            "note": restart_note,
            "backup": str(backup_path),
        }


    def _restart_jellyfin_for_platform(
        self, platform: str,
    ) -> tuple[str, bool]:
        """Restart Jellyfin and report (note, rollback_required).

        Compose path uses docker-py with health-check polling. K8s
        path runs ``kubectl rollout restart`` and waits with the
        kubectl rollout-status timer (faster + cluster-aware).
        Returns ``(human-readable note, True if rollback needed)``.
        """
        if platform == "k8s":
            return self._restart_jellyfin_k8s()
        return self._restart_jellyfin_compose()

    def _restart_jellyfin_compose(self) -> tuple[str, bool]:
        try:
            import docker
            client = docker.from_env()
            jf = client.containers.get("jellyfin")
            jf.restart(timeout=30)
            for _ in range(15):
                time.sleep(2)
                jf.reload()
                status = jf.status
                if status == "running":
                    health = (jf.attrs.get("State") or {}).get(
                        "Health", {},
                    ).get("Status", "")
                    if health in ("healthy", ""):
                        return ("Jellyfin restarted and running.", False)
                elif status in ("exited", "dead"):
                    return (
                        "Jellyfin failed to start after config change.",
                        True,
                    )
            return ("Jellyfin restarted (health check pending).", False)
        except Exception:  # noqa: BLE001 — docker errors aren't typed
            return (
                "Restart Jellyfin manually to apply changes.",
                False,
            )

    def _restart_jellyfin_k8s(self) -> tuple[str, bool]:
        import shutil
        import subprocess
        try:
            from media_stack.core.cli_common import kube_cmd
            tokens = list(kube_cmd())
        except Exception:  # noqa: BLE001
            return (
                "Restart Jellyfin manually (kubectl unreachable).",
                False,
            )
        if not tokens or not shutil.which(tokens[0]):
            return (
                "Restart Jellyfin manually (kubectl not on PATH).",
                False,
            )
        namespace = (
            os.environ.get("K8S_NAMESPACE", "") or "media-stack"
        ).strip()
        try:
            restart_proc = subprocess.run(
                [*tokens, "-n", namespace, "rollout", "restart",
                 "deploy/jellyfin"],
                check=False, capture_output=True, text=True, timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            return (
                "Restart Jellyfin manually (kubectl rollout failed).",
                False,
            )
        if restart_proc.returncode != 0:
            return (
                f"kubectl rollout restart failed: "
                f"{(restart_proc.stderr or '').strip()[:120]}",
                True,
            )
        try:
            status_proc = subprocess.run(
                [*tokens, "-n", namespace, "rollout", "status",
                 "deploy/jellyfin", "--timeout=60s"],
                check=False, capture_output=True, text=True, timeout=70,
            )
        except (OSError, subprocess.SubprocessError):
            return ("Jellyfin restart triggered (status pending).", False)
        if status_proc.returncode != 0:
            return (
                f"Jellyfin failed to roll out: "
                f"{(status_proc.stderr or status_proc.stdout or '').strip()[:120]}",
                True,
            )
        return ("Jellyfin restarted via kubectl rollout.", False)


_instance = JellyfinGpu()
check_jellyfin_gpu = _instance.check_jellyfin_gpu
build_compose_snippet = _instance.build_compose_snippet
build_k8s_snippet = _instance.build_k8s_snippet
enable_gpu_transcoding = _instance.enable_gpu_transcoding
