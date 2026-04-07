"""Disk usage and guardrail configuration services."""

from __future__ import annotations

import json
import os
from pathlib import Path
from shutil import disk_usage
from typing import Any

from media_stack.api.services._resolve import resolve_config_path


def get_disk() -> dict[str, Any]:
    """Check disk usage on media/config volumes."""
    paths_to_check: dict[str, str] = {
        "config": os.environ.get("CONFIG_ROOT", "/srv-config"),
    }
    for label, candidates in [
        ("media", [os.environ.get("MEDIA_ROOT", ""), "/srv-stack/media", "/media", "/data/media"]),
        ("torrents", ["/srv-stack/data/torrents", "/data/torrents", "/downloads/torrents"]),
        ("usenet", ["/srv-stack/data/usenet", "/data/usenet", "/downloads/usenet"]),
    ]:
        for p in candidates:
            if p and Path(p).exists():
                paths_to_check[label] = p
                break
    results: dict[str, Any] = {}
    for label, path_str in paths_to_check.items():
        path = Path(path_str)
        if path.exists():
            try:
                usage = disk_usage(path)
                results[label] = {
                    "path": str(path),
                    "total_bytes": usage.total,
                    "used_bytes": usage.used,
                    "free_bytes": usage.free,
                    "percent_used": round(usage.used / usage.total * 100, 1) if usage.total else 0,
                }
            except Exception as exc:
                results[label] = {"path": str(path), "error": str(exc)[:80]}
        else:
            results[label] = {"path": str(path), "error": "path not found"}

    guardrails = _load_guardrail_config()
    return {"disk": results, "guardrails": guardrails}


def _load_guardrail_config() -> dict[str, Any]:
    """Load disk guardrail thresholds from bootstrap config."""
    guardrails: dict[str, Any] = {"enabled": False}
    resolved_cfg = resolve_config_path()
    if not resolved_cfg:
        return guardrails
    try:
        cfg = json.loads(Path(resolved_cfg).read_text(encoding="utf-8"))
        gc = cfg.get("disk_guardrails") or {}
        qc = gc.get("qbit_cleanup") or {}
        guardrails = {
            "enabled": bool(gc.get("enabled", False)),
            "max_used_percent": float(gc.get("max_used_percent", 65)),
            "target_used_percent": float(gc.get("target_used_percent", 58)),
            "monitor_path": str(gc.get("monitor_path", "")),
            "qbit_cleanup": {
                "enabled": bool(qc.get("enabled", True)),
                "min_completion_age_hours": float(qc.get("min_completion_age_hours", 36)),
                "min_ratio": float(qc.get("min_ratio", 1.0)),
                "min_seeding_time_minutes": int(qc.get("min_seeding_time_minutes", 720)),
                "max_delete_per_run": int(qc.get("max_delete_per_run", 80)),
                "delete_files": bool(qc.get("delete_files", True)),
                "categories": list(qc.get("categories", [])),
            },
        }
    except Exception:
        pass
    return guardrails


def update_guardrails(updates: dict[str, Any]) -> dict[str, Any]:
    """Update disk guardrail settings in the bootstrap config JSON."""
    config_path = resolve_config_path()
    if not config_path:
        return {"error": "Config file not found"}
    try:
        cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
        gc = cfg.setdefault("disk_guardrails", {})
        allowed = {"enabled", "max_used_percent", "target_used_percent", "monitor_path"}
        qbit_allowed = {"enabled", "min_completion_age_hours", "min_ratio",
                        "min_seeding_time_minutes", "max_delete_per_run", "delete_files"}
        changed = []
        for k, v in updates.items():
            if k in allowed:
                gc[k] = v
                changed.append(k)
            elif k.startswith("qbit_") and k[5:] in qbit_allowed:
                gc.setdefault("qbit_cleanup", {})[k[5:]] = v
                changed.append(k)
        if not changed:
            return {"status": "no_changes"}
        Path(config_path).write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        return {"status": "updated", "changed": changed}
    except Exception as exc:
        return {"error": str(exc)[:200]}
