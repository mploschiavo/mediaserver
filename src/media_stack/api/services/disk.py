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
                # Detect filesystem type
                fstype = ""
                try:
                    with open("/proc/mounts") as f:
                        for line in f:
                            parts = line.split()
                            if len(parts) >= 3 and parts[1] == str(path) or str(path).startswith(parts[1] + "/"):
                                fstype = parts[2]
                except Exception:
                    pass
                entry: dict[str, Any] = {
                    "path": str(path),
                    "total_bytes": usage.total,
                    "used_bytes": usage.used,
                    "free_bytes": usage.free,
                    "percent_used": round(usage.used / usage.total * 100, 1) if usage.total else 0,
                }
                if fstype:
                    entry["fstype"] = fstype
                if fstype in ("tmpfs", "ramfs"):
                    entry["warning"] = "RAM-backed filesystem — data lost on reboot"
                results[label] = entry
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


def preview_cleanup() -> dict[str, Any]:
    """Preview what torrents would be deleted by guardrail cleanup (dry run)."""
    import http.cookiejar
    import time
    import urllib.request

    gc = _load_guardrail_config()
    if not gc.get("enabled"):
        return {"candidates": [], "message": "Guardrails disabled"}
    qc = gc.get("qbit_cleanup") or {}
    if not qc.get("enabled"):
        return {"candidates": [], "message": "qBit cleanup disabled"}

    min_age_hours = float(qc.get("min_completion_age_hours", 36))
    min_ratio = float(qc.get("min_ratio", 1.0))
    min_seed_min = int(qc.get("min_seeding_time_minutes", 720))
    max_pct = float(gc.get("max_used_percent", 65))

    # Get current disk usage
    disk = get_disk()
    over_threshold = False
    for _, v in disk.get("disk", {}).items():
        if isinstance(v, dict) and v.get("percent_used", 0) > max_pct:
            over_threshold = True
            break

    # List completed torrents
    try:
        user = os.environ.get("STACK_ADMIN_USERNAME", "admin")
        pw = os.environ.get("STACK_ADMIN_PASSWORD", "media-stack")
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        login = urllib.request.Request(
            "http://qbittorrent:8080/api/v2/auth/login",
            data=f"username={user}&password={pw}".encode(),
        )
        opener.open(login, timeout=5)
        req = urllib.request.Request("http://qbittorrent:8080/api/v2/torrents/info?filter=completed")
        with opener.open(req, timeout=5) as resp:
            import json as _json
            torrents = _json.loads(resp.read())
    except Exception as exc:
        return {"candidates": [], "error": str(exc)[:80]}

    now = int(time.time())
    candidates = []
    for t in torrents:
        completion_on = t.get("completion_on", 0) or 0
        age_hours = max(0, (now - completion_on) / 3600) if completion_on > 0 else 0
        ratio = t.get("ratio", 0) or 0
        seed_min = int((t.get("seeding_time", 0) or 0) / 60)
        meets_age = age_hours >= min_age_hours
        meets_ratio = ratio >= min_ratio
        meets_seed = seed_min >= min_seed_min
        if meets_age and (meets_ratio or meets_seed):
            candidates.append({
                "name": t.get("name", "")[:80],
                "size": t.get("size", 0),
                "category": t.get("category", ""),
                "age_hours": round(age_hours, 1),
                "ratio": round(ratio, 2),
                "seed_minutes": seed_min,
            })

    candidates.sort(key=lambda x: x.get("age_hours", 0), reverse=True)
    return {
        "candidates": candidates[:50],
        "total_candidates": len(candidates),
        "over_threshold": over_threshold,
        "max_used_percent": max_pct,
        "would_trigger": over_threshold and len(candidates) > 0,
    }
