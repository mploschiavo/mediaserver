"""Maintenance utilities: config snapshots, stale file pruning.

Extracted from controller_main.py for maintainability.
"""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import argparse
import os
import re
import time
from pathlib import Path
from typing import Any

from media_stack.core.service_registry.registry import SERVICES
import logging




class MaintenanceService:
    """Wraps maintenance utility functions."""

    def take_config_snapshot(self, args: argparse.Namespace) -> None:
        """Save a timestamped snapshot of all service config files."""
        import json as _json

        config_root = Path(getattr(args, "config_root", os.environ.get("CONFIG_ROOT", "/srv-config")))
        snapshot_dir = config_root / ".snapshots"
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        snapshot: dict[str, str] = {}
        patterns = _snapshot_config_paths()
        for app, rel in patterns:
            path = config_root / app / rel
            if path.is_file():
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                    text = re.sub(r"<ApiKey>[^<]+</ApiKey>", "<ApiKey>***</ApiKey>", text)
                    text = re.sub(r"api_key\s*=\s*\S+", "api_key = ***", text)
                    text = re.sub(r'"apiKey"\s*:\s*"[^"]+"', '"apiKey": "***"', text)
                    snapshot[f"{app}/{rel}"] = text
                except Exception as exc:
                    log_swallowed(exc)

        ts = time.strftime("%Y%m%dT%H%M%S")
        out = snapshot_dir / f"snapshot-{ts}.json"
        out.write_text(_json.dumps(snapshot, indent=2), encoding="utf-8")

        existing = sorted(snapshot_dir.glob("snapshot-*.json"), reverse=True)
        for old in existing[24:]:
            old.unlink(missing_ok=True)

    def prune_stale_files(self, args: argparse.Namespace, log: Any) -> None:
        """Clean up files that grow without bounds: XMLTV guides, old logs, temp files."""
        config_root = Path(getattr(args, "config_root", os.environ.get("CONFIG_ROOT", "/srv-config")))
        pruned = 0

        # Media server XMLTV guide directories — derived from registry (category=media).
        media_server_ids = [s.id for s in SERVICES if s.category == "media" and s.host]
        xmltv_dirs = [
            config_root.parent / "data" / "transcode" / "xmltv",
            Path("/srv-stack/data/transcode/xmltv"),
            Path("/cache/xmltv"),
        ]
        for ms_id in media_server_ids:
            xmltv_dirs.append(config_root / ms_id / "data" / "xmltv")
        for xmltv_dir in xmltv_dirs:
            if xmltv_dir.is_dir():
                xmls = sorted(xmltv_dir.glob("*.xml"), key=lambda f: f.stat().st_mtime, reverse=True)
                for old in xmls[2:]:
                    try:
                        sz = old.stat().st_size
                        old.unlink()
                        pruned += 1
                        log(f"[INFO] Pruned stale XMLTV guide: {old.name} ({sz // 1048576}MB)")
                    except Exception as exc:
                        log_swallowed(exc)

        # Media server log directories.
        for ms_id in media_server_ids:
            ms_log_dir = config_root / ms_id / "log"
            if ms_log_dir.is_dir():
                logs = sorted(ms_log_dir.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
                for old in logs[5:]:
                    try:
                        old.unlink()
                        pruned += 1
                    except Exception as exc:
                        log_swallowed(exc)

        # Arr services (XML config format) store logs in <id>/logs/.
        arr_ids = tuple(s.id for s in SERVICES if s.api_key_format == "xml")
        for app in arr_ids:
            log_dir = config_root / app / "logs"
            if log_dir.is_dir():
                app_logs = sorted(log_dir.glob("*.txt"), key=lambda f: f.stat().st_mtime, reverse=True)
                app_logs += sorted(log_dir.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
                for old in app_logs[5:]:
                    try:
                        old.unlink()
                        pruned += 1
                    except Exception as exc:
                        log_swallowed(exc)

        if pruned:
            log(f"[INFO] Stale file cleanup: pruned {pruned} files")


    @staticmethod
    def _snapshot_config_paths() -> list[tuple[str, str]]:
        """Build (app_id, relative_config_path) pairs from the service registry.
    
        Only includes text-based config files (not binary formats like sqlite).
        """
        return [
            (s.id, s.api_key_config.split("/", 1)[1])
            for s in SERVICES
            if s.api_key_config and s.api_key_format != "sqlite"
        ]


_instance = MaintenanceService()
take_config_snapshot = _instance.take_config_snapshot
prune_stale_files = _instance.prune_stale_files
_snapshot_config_paths = _instance._snapshot_config_paths
