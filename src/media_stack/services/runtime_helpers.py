"""Reusable runtime helpers extracted from bootstrap entrypoint."""

from __future__ import annotations

import json
import os
from urllib import parse, request

from media_stack.adapters.common import normalize_url as _lib_normalize_url


def to_float(value, fallback=None):
    try:
        if value is None:
            return fallback
        text = str(value).strip()
        if text == "":
            return fallback
        return float(text)
    except Exception:
        return fallback


def disk_usage_percent(path):
    st = os.statvfs(path)
    total = int(st.f_blocks) * int(st.f_frsize)
    avail = int(st.f_bavail) * int(st.f_frsize)
    if total <= 0:
        return 0.0, total, avail
    used = total - avail
    used_pct = (float(used) * 100.0) / float(total)
    return used_pct, total, avail


def fmt_bytes(num):
    value = float(max(0, int(num)))
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    idx = 0
    while value >= 1024.0 and idx < len(units) - 1:
        value /= 1024.0
        idx += 1
    return f"{value:.2f} {units[idx]}"


def qbit_list_completed_torrents(opener, base_url):
    req = request.Request(
        f"{_lib_normalize_url(base_url)}/api/v2/torrents/info?"
        f"{parse.urlencode({'filter': 'completed'})}",
        method="GET",
    )
    with opener.open(req, timeout=25) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    try:
        payload = json.loads(body)
    except Exception as exc:
        raise RuntimeError(
            f"Torrent client: failed parsing completed torrents payload: {exc}"
        ) from exc
    if isinstance(payload, list):
        return payload
    raise RuntimeError("Torrent client: completed torrent payload was not a list.")


def qbit_list_torrents(opener, base_url, filter_value="all"):
    req = request.Request(
        f"{_lib_normalize_url(base_url)}/api/v2/torrents/info?"
        f"{parse.urlencode({'filter': str(filter_value or 'all')})}",
        method="GET",
    )
    with opener.open(req, timeout=25) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    try:
        payload = json.loads(body)
    except Exception as exc:
        raise RuntimeError(f"Torrent client: failed parsing torrents payload: {exc}") from exc
    if isinstance(payload, list):
        return payload
    raise RuntimeError("Torrent client: torrents payload was not a list.")


def qbit_delete_torrents(opener, base_url, hashes, delete_files=True):
    if not hashes:
        return
    data = parse.urlencode(
        {
            "hashes": "|".join(hashes),
            "deleteFiles": "true" if delete_files else "false",
        }
    ).encode("utf-8")
    req = request.Request(
        f"{_lib_normalize_url(base_url)}/api/v2/torrents/delete",
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with opener.open(req, timeout=30):
        pass
