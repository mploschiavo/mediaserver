"""SABnzbd preflight: config reconciliation via file I/O.

Replaces the compose_preflight.py docker-exec-based approach. Uses:
- Direct file I/O to /srv-config/sabnzbd/ for INI editing
- Docker SDK only for container restart after config changes
- HTTP probe for readiness verification
"""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import re
import shutil
import time
from pathlib import Path
from typing import Any

import requests
import logging

from media_stack.api.services.registry import service_internal_url


class SabnzbdHttpPreflight:

    @staticmethod
    def _wait_ready(base_url: str, timeout: int = 60) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                resp = requests.get(base_url, timeout=5)
                if resp.status_code == 200:
                    return True
            except requests.ConnectionError:
                logging.getLogger("media_stack").debug("[DEBUG] Swallowed exception", exc_info=True)
            time.sleep(3)
        return False

    @staticmethod
    def _restart_container(container_name: str = "sabnzbd") -> None:
        """Restart container — tries Docker SDK (compose), then K8s pod delete."""
        try:
            import docker

            client = docker.from_env()
            container = client.containers.get(container_name)
            container.restart(timeout=15)
            return
        except Exception as exc:
            log_swallowed(exc)
        try:
            from kubernetes import client, config

            try:
                config.load_incluster_config()
            except config.ConfigException:
                config.load_kube_config()

            v1 = client.CoreV1Api()
            namespace = __import__("os").environ.get("K8S_NAMESPACE", "media-stack")
            pods = v1.list_namespaced_pod(
                namespace=namespace,
                label_selector=f"app={container_name}",
            )
            for pod in pods.items:
                v1.delete_namespaced_pod(name=pod.metadata.name, namespace=namespace)
            return
        except Exception as exc:
            raise RuntimeError(f"Failed to restart {container_name}: {exc}") from exc

    @staticmethod
    def _update_ini_value(
        lines: list[str],
        key: str,
        value: str,
        section: str = "misc",
    ) -> tuple[list[str], bool]:
        """Update a key=value in an INI file's lines. Returns (new_lines, changed)."""
        in_section = False
        key_pattern = re.compile(rf"^\s*{re.escape(key)}\s*=", flags=re.IGNORECASE)
        found = False
        changed = False
        result: list[str] = []

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("["):
                in_section = stripped.lower() == f"[{section.lower()}]"
            if in_section and key_pattern.match(stripped):
                new_line = f"{key} = {value}"
                if stripped != new_line:
                    result.append(new_line)
                    changed = True
                else:
                    result.append(line)
                found = True
            else:
                result.append(line)

        if not found and section:
            # Append to the section.
            in_target = False
            inserted = False
            final: list[str] = []
            for line in result:
                final.append(line)
                stripped = line.strip()
                if stripped.lower() == f"[{section.lower()}]":
                    in_target = True
                elif stripped.startswith("[") and in_target and not inserted:
                    final.insert(-1, f"{key} = {value}")
                    inserted = True
                    changed = True
            if not inserted:
                final.append(f"{key} = {value}")
                changed = True
            result = final

        return result, changed

    def run_preflight(self,
        *,
        sab_url: str | None = None,
        config_root: str = "/srv-config",
        container_name: str = "sabnzbd",
        host_whitelist: str = "",
        local_ranges: str = "",
        wait_timeout: int = 60,
        log: Any = None,
        **kwargs: Any,
    ) -> dict[str, str]:
        if sab_url is None:
            sab_url = service_internal_url("sabnzbd")
        """Reconcile SABnzbd host_whitelist and local_ranges config.

        Edits sabnzbd.ini directly via the shared config mount, then restarts
        the container if changes were made.

        Returns empty dict (no env vars to propagate).
        """

        def info(msg: str) -> None:
            if log:
                log(msg)

        ini_path = Path(config_root) / "sabnzbd" / "sabnzbd.ini"
        if not ini_path.exists():
            info(f"SABnzbd preflight: config not found at {ini_path}, skipping")
            return {}

        text = ini_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        any_changed = False

        # Ensure host_whitelist includes required hostnames.
        if host_whitelist:
            current_wl = ""
            for line in lines:
                if re.match(r"^\s*host_whitelist\s*=", line, flags=re.IGNORECASE):
                    current_wl = line.split("=", 1)[1].strip()
                    break
            needed = {h.strip().lower() for h in host_whitelist.split(",") if h.strip()}
            existing = {h.strip().lower() for h in current_wl.split(",") if h.strip()}
            if not needed.issubset(existing):
                merged = sorted(existing | needed)
                lines, changed = _update_ini_value(lines, "host_whitelist", ", ".join(merged))
                if changed:
                    any_changed = True
                    info(f"SABnzbd preflight: updated host_whitelist → {', '.join(merged)}")

        # Ensure local_ranges includes required ranges.
        if local_ranges:
            current_lr = ""
            for line in lines:
                if re.match(r"^\s*local_ranges\s*=", line, flags=re.IGNORECASE):
                    current_lr = line.split("=", 1)[1].strip()
                    break
            needed = {r.strip() for r in local_ranges.split(",") if r.strip()}
            existing = {r.strip() for r in current_lr.split(",") if r.strip()}
            if not needed.issubset(existing):
                merged = sorted(existing | needed)
                lines, changed = _update_ini_value(lines, "local_ranges", ", ".join(merged))
                if changed:
                    any_changed = True
                    info(f"SABnzbd preflight: updated local_ranges → {', '.join(merged)}")

        if not any_changed:
            info("SABnzbd preflight: config already aligned, no changes needed")
            return {}

        # Write config with backup.
        backup_path = ini_path.with_suffix(f".ini.bak.{int(time.time())}")
        shutil.copy2(ini_path, backup_path)
        ini_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        info(f"SABnzbd preflight: wrote updated config (backup at {backup_path.name})")

        # Restart container to pick up changes.
        info("SABnzbd preflight: restarting container")
        _restart_container(container_name)

        info(f"SABnzbd preflight: waiting for {sab_url}")
        if not _wait_ready(sab_url, timeout=wait_timeout):
            raise RuntimeError(f"SABnzbd not reachable at {sab_url} after restart")

        info("SABnzbd preflight: ready after config update")
        return {}


_instance = SabnzbdHttpPreflight()
run_preflight = _instance.run_preflight
_restart_container = _instance._restart_container
_update_ini_value = _instance._update_ini_value
_wait_ready = _instance._wait_ready
