"""qB IP filter refresh helpers for media hygiene operations."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import request


def run_qbit_ipfilter_refresh(
    ops,
    hygiene_cfg: dict[str, Any],
    qbit_cfg: dict[str, Any],
    qb_username: str,
    qb_password: str,
) -> dict[str, Any]:
    ipf_cfg = hygiene_cfg.get("qbit_ipfilter") or {}
    enabled = ops.bool_cfg(ipf_cfg, "enabled", False)
    summary: dict[str, Any] = {
        "enabled": enabled,
        "downloaded": False,
        "applied": False,
        "skipped_reason": "",
        "source_url": "",
        "target_path": "",
        "bytes": 0,
    }
    if not enabled:
        return summary

    if not str(qb_username or "").strip() or not str(qb_password or "").strip():
        raise RuntimeError(
            "qB IP filter refresh requires qB credentials (STACK_ADMIN_USERNAME/STACK_ADMIN_PASSWORD)."
        )

    qbit_url = ops.normalize_url((qbit_cfg or {}).get("url", "http://qbittorrent:8080"))
    required = ops.bool_cfg(ipf_cfg, "required", False)
    apply_existing_on_failure = ops.bool_cfg(ipf_cfg, "apply_existing_on_download_failure", True)
    source_url = str(
        ipf_cfg.get("url")
        or ipf_cfg.get("source_url")
        or "https://github.com/DavidMoore/ipfilter/releases/download/lists/ipfilter.dat"
    ).strip()
    fallback_urls = [
        str(x).strip() for x in ops.coerce_list(ipf_cfg.get("fallback_urls")) if str(x).strip()
    ]
    urls: list[str] = []
    if source_url:
        urls.append(source_url)
    for item in fallback_urls:
        if item not in urls:
            urls.append(item)

    target_path = str(ipf_cfg.get("target_path") or "/srv-stack/data/torrents/ipfilter.dat").strip()
    qbit_filter_path = str(ipf_cfg.get("qbit_filter_path") or "/data/torrents/ipfilter.dat").strip()
    mirror_target_paths = [
        str(x).strip()
        for x in ops.coerce_list(ipf_cfg.get("mirror_target_paths") or [])
        if str(x).strip()
    ]
    target_candidates = [target_path]
    for mirror in mirror_target_paths:
        if mirror not in target_candidates:
            target_candidates.append(mirror)
    state_path = str(
        ipf_cfg.get("state_path") or "/srv-stack/data/torrents/.ipfilter-refresh-state.json"
    ).strip()
    timeout_seconds = ops.to_int(ipf_cfg.get("download_timeout_seconds"), 30) or 30
    min_valid_bytes = ops.to_int(ipf_cfg.get("min_valid_bytes"), 1024) or 1024
    min_refresh_interval_hours = ops.to_float(ipf_cfg.get("min_refresh_interval_hours"), 24.0) or 24.0

    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    mirror_paths = [Path(p) for p in target_candidates[1:]]
    for mirror in mirror_paths:
        try:
            mirror.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            continue
    state_file = Path(state_path)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    summary["target_path"] = str(target)

    state: dict[str, Any] = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                state = {}
        except Exception:
            state = {}

    now_epoch = int(time.time())
    min_refresh_seconds = max(0, int(min_refresh_interval_hours * 3600))
    last_success = ops.to_int(state.get("last_success_epoch"), 0) or 0
    downloaded = False

    if min_refresh_seconds > 0 and last_success > 0 and (now_epoch - last_success) < min_refresh_seconds:
        summary["skipped_reason"] = "min_refresh_interval"
        summary["source_url"] = str(state.get("source_url") or source_url)
        summary["bytes"] = ops.to_int(state.get("bytes"), 0) or 0
        ops.log(
            "[INFO] qB IP filter: skipping download due to min refresh interval "
            f"(last_success={last_success}, min_hours={min_refresh_interval_hours})."
        )
    else:
        errors: list[str] = []
        data = b""
        selected_url = ""
        for candidate in urls:
            selected_url = candidate
            try:
                req = request.Request(
                    candidate,
                    method="GET",
                    headers={"User-Agent": "media-stack-ipfilter/1.0"},
                )
                with request.urlopen(req, timeout=timeout_seconds) as resp:
                    data = resp.read()
                if len(data) < min_valid_bytes:
                    raise RuntimeError(
                        f"Downloaded file too small ({len(data)} bytes, expected >= {min_valid_bytes})."
                    )
                tmp_path = target.with_name(f"{target.name}.tmp")
                tmp_path.write_bytes(data)
                os.replace(tmp_path, target)
                for mirror in mirror_paths:
                    try:
                        mirror_tmp = mirror.with_name(f"{mirror.name}.tmp")
                        mirror_tmp.write_bytes(data)
                        os.replace(mirror_tmp, mirror)
                    except Exception as mirror_exc:
                        ops.log(
                            f"[WARN] qB IP filter: mirror write failed for {mirror} " f"({mirror_exc})"
                        )
                downloaded = True
                summary["downloaded"] = True
                summary["source_url"] = selected_url
                summary["bytes"] = len(data)
                break
            except Exception as exc:
                errors.append(f"{candidate}: {exc}")
                continue

        if not downloaded:
            cached_target: Path | None = None
            for candidate in [target] + mirror_paths:
                if candidate.exists():
                    cached_target = candidate
                    break
            if cached_target is not None and apply_existing_on_failure:
                summary["skipped_reason"] = "source_unavailable_using_cached_filter"
                summary["source_url"] = str(state.get("source_url") or source_url)
                summary["bytes"] = cached_target.stat().st_size
                summary["target_path"] = str(cached_target)
                ops.log(
                    "[WARN] qB IP filter: download source unavailable; using cached filter file "
                    f"at {cached_target}."
                )
                if errors:
                    ops.log(f"[WARN] qB IP filter: download errors: {' | '.join(errors)}")
            else:
                message = (
                    "qB IP filter: unable to download filter and no usable cached copy exists "
                    f"(targets={target_candidates}, urls={urls}, errors={errors})."
                )
                if required:
                    raise RuntimeError(message)
                ops.log(f"[WARN] {message}")
                return summary

    opener = ops.qbit_login(qbit_url, qb_username, qb_password)
    ops.qbit_set_preferences(
        opener,
        qbit_url,
        {
            "ip_filter_enabled": True,
            "ip_filter_path": qbit_filter_path,
        },
    )
    summary["applied"] = True
    ops.log(
        "[OK] qB IP filter: preferences applied "
        f"(enabled=True, path={qbit_filter_path}, downloaded={summary['downloaded']})."
    )

    if downloaded:
        now = datetime.now(timezone.utc)
        state.update(
            {
                "last_success_epoch": int(now.timestamp()),
                "last_success_iso": now.isoformat(),
                "source_url": summary["source_url"],
                "bytes": summary["bytes"],
                "target_path": str(target),
                "qbit_filter_path": qbit_filter_path,
            }
        )
        try:
            state_file.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            ops.log(f"[WARN] qB IP filter: failed writing state file {state_file} ({exc})")
    return summary

