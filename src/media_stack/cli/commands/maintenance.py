"""Maintenance utilities: config snapshots, stale file pruning.

Extracted from controller_main.py for maintainability.
"""

from __future__ import annotations

import argparse
import os
import re
import time
from pathlib import Path
from typing import Any


def take_config_snapshot(args: argparse.Namespace) -> None:
    """Save a timestamped snapshot of all service config files."""
    import json as _json

    config_root = Path(getattr(args, "config_root", os.environ.get("CONFIG_ROOT", "/srv-config")))
    snapshot_dir = config_root / ".snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    snapshot: dict[str, str] = {}
    patterns = [
        ("sonarr", "config.xml"), ("radarr", "config.xml"), ("lidarr", "config.xml"),
        ("readarr", "config.xml"), ("prowlarr", "config.xml"),
        ("bazarr", "config/config.yaml"), ("sabnzbd", "sabnzbd.ini"),
        ("jellyseerr", "settings.json"), ("homepage", "services.yaml"),
        ("tautulli", "config.ini"),
    ]
    for app, rel in patterns:
        path = config_root / app / rel
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                text = re.sub(r"<ApiKey>[^<]+</ApiKey>", "<ApiKey>***</ApiKey>", text)
                text = re.sub(r"api_key\s*=\s*\S+", "api_key = ***", text)
                text = re.sub(r'"apiKey"\s*:\s*"[^"]+"', '"apiKey": "***"', text)
                snapshot[f"{app}/{rel}"] = text
            except Exception:
                pass

    ts = time.strftime("%Y%m%dT%H%M%S")
    out = snapshot_dir / f"snapshot-{ts}.json"
    out.write_text(_json.dumps(snapshot, indent=2), encoding="utf-8")

    existing = sorted(snapshot_dir.glob("snapshot-*.json"), reverse=True)
    for old in existing[24:]:
        old.unlink(missing_ok=True)


def prune_stale_files(args: argparse.Namespace, log: Any) -> None:
    """Clean up files that grow without bounds: XMLTV guides, old logs, temp files."""
    config_root = Path(getattr(args, "config_root", os.environ.get("CONFIG_ROOT", "/srv-config")))
    pruned = 0

    for xmltv_dir in [
        config_root.parent / "data" / "transcode" / "xmltv",
        config_root / "jellyfin" / "data" / "xmltv",
        Path("/srv-stack/data/transcode/xmltv"),
        Path("/cache/xmltv"),
    ]:
        if xmltv_dir.is_dir():
            xmls = sorted(xmltv_dir.glob("*.xml"), key=lambda f: f.stat().st_mtime, reverse=True)
            for old in xmls[2:]:
                try:
                    sz = old.stat().st_size
                    old.unlink()
                    pruned += 1
                    log(f"[INFO] Pruned stale XMLTV guide: {old.name} ({sz // 1048576}MB)")
                except Exception:
                    pass

    jf_log_dir = config_root / "jellyfin" / "log"
    if jf_log_dir.is_dir():
        logs = sorted(jf_log_dir.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
        for old in logs[5:]:
            try:
                old.unlink()
                pruned += 1
            except Exception:
                pass

    for app in ("prowlarr", "sonarr", "radarr", "lidarr", "readarr"):
        log_dir = config_root / app / "logs"
        if log_dir.is_dir():
            app_logs = sorted(log_dir.glob("*.txt"), key=lambda f: f.stat().st_mtime, reverse=True)
            app_logs += sorted(log_dir.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
            for old in app_logs[5:]:
                try:
                    old.unlink()
                    pruned += 1
                except Exception:
                    pass

    if pruned:
        log(f"[INFO] Stale file cleanup: pruned {pruned} files")
