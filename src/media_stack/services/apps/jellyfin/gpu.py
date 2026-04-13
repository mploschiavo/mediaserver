"""Jellyfin GPU detection and hardware-transcoding auto-configuration.

Moved from ``api/services/ops.py`` to keep Jellyfin-specific system.xml
parsing and container inspection in the app layer.
"""

from __future__ import annotations

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

        Returns a dict with ``jellyfin_has_gpu`` (bool) plus any
        ``jellyfin_configured`` / ``jellyfin_hw_type`` keys derived
        from the Jellyfin ``system.xml`` on disk.
        """
        result: dict[str, Any] = {"jellyfin_has_gpu": False, "jellyfin_configured": False}

        try:
            jf = docker_client.containers.get("jellyfin")
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
            logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
            pass

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
                logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
                pass

        return result

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

        # Restart Jellyfin and verify it comes back healthy
        restart_note = ""
        rollback = False
        try:
            import docker
            client = docker.from_env()
            jf = client.containers.get("jellyfin")
            jf.restart(timeout=30)
            # Wait up to 30s for Jellyfin to come back healthy
            for _ in range(15):
                time.sleep(2)
                jf.reload()
                status = jf.status
                if status == "running":
                    health = (jf.attrs.get("State") or {}).get("Health", {}).get("Status", "")
                    if health in ("healthy", ""):
                        restart_note = "Jellyfin restarted and running."
                        break
                elif status in ("exited", "dead"):
                    rollback = True
                    restart_note = "Jellyfin failed to start after config change."
                    break
            else:
                restart_note = "Jellyfin restarted (health check pending)."
        except Exception:
            restart_note = "Restart Jellyfin manually to apply changes."

        if rollback:
            # Roll back to backup
            try:
                system_xml.write_text(backup_path.read_text(encoding="utf-8"), encoding="utf-8")
                import docker
                client = docker.from_env()
                client.containers.get("jellyfin").restart(timeout=30)
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


_instance = JellyfinGpu()
check_jellyfin_gpu = _instance.check_jellyfin_gpu
build_compose_snippet = _instance.build_compose_snippet
enable_gpu_transcoding = _instance.enable_gpu_transcoding
