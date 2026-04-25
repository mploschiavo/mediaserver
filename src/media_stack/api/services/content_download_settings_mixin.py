"""Download-client-settings mixin for ``ContentService``.

Split out of ``content.py`` so the main ``ContentService`` class stays
under the 500-line god-class ratchet. Everything in here is the
dashboard's "Downloads" control-panel surface: read-only snapshots
for qBittorrent + Jellyfin scan schedule, and the matching mutate
methods that fire an on-demand library scan.

Helpers from the main ``content`` module are imported lazily inside
each method to avoid a circular import — at class-body evaluation
time ``content`` is still in the middle of importing this mixin.
"""

from __future__ import annotations


import json
import os
import urllib.request
from typing import Any

from .health import discover_api_keys
from .registry import SERVICE_MAP


class _ContentDownloadSettingsMixin:
    """qBittorrent + Jellyfin scan control for ``ContentService``."""

    def get_download_client_settings(self) -> dict[str, Any]:
        """Get qBittorrent download limits and Jellyfin scan schedule."""
        from .content import _find_scan_task, _summarize_scan_task
        result: dict[str, Any] = {"torrent": {}, "jellyfin_scan": {}}
        # qBittorrent settings
        try:
            import http.cookiejar
            cj = http.cookiejar.CookieJar()
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
            user = os.environ.get("STACK_ADMIN_USERNAME", "admin")
            pw = os.environ.get("STACK_ADMIN_PASSWORD", "media-stack")
            svc = SERVICE_MAP.get("qbittorrent")
            if svc:
                opener.open(urllib.request.Request(
                    f"http://{svc.host}:{svc.port}/api/v2/auth/login",
                    data=f"username={user}&password={pw}".encode(),
                ))
                prefs = json.loads(opener.open(f"http://{svc.host}:{svc.port}/api/v2/app/preferences").read())
                result["torrent"] = {
                    "max_active_downloads": prefs.get("max_active_downloads", 3),
                    "max_active_torrents": prefs.get("max_active_torrents", 5),
                    "max_active_uploads": prefs.get("max_active_uploads", 3),
                    "dl_limit_mbps": round(prefs.get("dl_limit", 0) / 1024 / 1024, 1) if prefs.get("dl_limit") else 0,
                    "up_limit_mbps": round(prefs.get("up_limit", 0) / 1024 / 1024, 1) if prefs.get("up_limit") else 0,
                    "queueing_enabled": prefs.get("queueing_enabled", True),
                }
        except Exception as exc:
            result["torrent"]["error"] = str(exc)[:80]
        # Jellyfin scan schedule
        try:
            api_key = discover_api_keys().get("jellyfin", "")
            ms = SERVICE_MAP.get("jellyfin")
            if ms and api_key:
                tasks = json.loads(urllib.request.urlopen(
                    f"http://{ms.host}:{ms.port}/ScheduledTasks?api_key={api_key}", timeout=5
                ).read())
                scan_task = _find_scan_task(tasks)
                if scan_task is not None:
                    result["jellyfin_scan"] = _summarize_scan_task(scan_task)
        except Exception as exc:
            result["jellyfin_scan"]["error"] = str(exc)[:80]
        return result

    def update_download_client_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        """Update qBittorrent limits and/or trigger Jellyfin scan."""
        from .content import _JSON_MIME, _update_jellyfin_scan_interval
        results: dict[str, Any] = {}
        # Update qBittorrent
        torrent = settings.get("torrent", {})
        if torrent:
            try:
                import http.cookiejar
                cj = http.cookiejar.CookieJar()
                opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
                user = os.environ.get("STACK_ADMIN_USERNAME", "admin")
                pw = os.environ.get("STACK_ADMIN_PASSWORD", "media-stack")
                svc = SERVICE_MAP.get("qbittorrent")
                if svc:
                    opener.open(urllib.request.Request(
                        f"http://{svc.host}:{svc.port}/api/v2/auth/login",
                        data=f"username={user}&password={pw}".encode(),
                    ))
                    prefs = {}
                    if "max_active_downloads" in torrent:
                        prefs["max_active_downloads"] = int(torrent["max_active_downloads"])
                    if "max_active_torrents" in torrent:
                        prefs["max_active_torrents"] = int(torrent["max_active_torrents"])
                    if "max_active_uploads" in torrent:
                        prefs["max_active_uploads"] = int(torrent["max_active_uploads"])
                    if "dl_limit_mbps" in torrent:
                        prefs["dl_limit"] = int(float(torrent["dl_limit_mbps"]) * 1024 * 1024)
                    if "up_limit_mbps" in torrent:
                        prefs["up_limit"] = int(float(torrent["up_limit_mbps"]) * 1024 * 1024)
                    if prefs:
                        req = urllib.request.Request(
                            f"http://{svc.host}:{svc.port}/api/v2/app/setPreferences",
                            data=f"json={json.dumps(prefs)}".encode(),
                        )
                        opener.open(req)
                        results["torrent"] = {"status": "updated", "settings": prefs}
            except Exception as exc:
                results["torrent"] = {"error": str(exc)[:80]}
        # "Scan Library" — fire BOTH steps: (1) tell each *arr to
        # scan its completed-downloads path so anything qBit finished
        # (or that the user dropped in directly) gets imported into
        # ``/media/<cat>/``, then (2) ask Jellyfin to re-index those
        # paths. Step (1) alone wouldn't update Jellyfin's library
        # (it watches /media not /data); step (2) alone wouldn't
        # find files still sitting in qBit's completed dir. Doing
        # both makes the button match the user's mental model:
        # "find every new thing and surface it." (v1.0.144.)
        if settings.get("scan_now"):
            keys = discover_api_keys()
            arr_specs = [
                ("sonarr",  "v3", "DownloadedEpisodesScan", "/data/torrents/completed/tv"),
                ("radarr",  "v3", "DownloadedMoviesScan",   "/data/torrents/completed/movies"),
                ("lidarr",  "v1", "DownloadedAlbumsScan",   "/data/torrents/completed/music"),
                ("readarr", "v1", "DownloadedBooksScan",    "/data/torrents/completed/books"),
            ]
            arr_results: dict[str, str] = {}
            for app, ver, cmd, path in arr_specs:
                key = keys.get(app, "")
                svc = SERVICE_MAP.get(app)
                if not svc or not key:
                    arr_results[app] = "skipped (no key)"
                    continue
                try:
                    body = json.dumps({"name": cmd, "path": path}).encode()
                    req = urllib.request.Request(
                        f"http://{svc.host}:{svc.port}/api/{ver}/command",
                        data=body, method="POST",
                        headers={"X-Api-Key": key, "Content-Type": _JSON_MIME},
                    )
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        arr_results[app] = f"queued ({resp.status})"
                except Exception as exc:
                    arr_results[app] = str(exc)[:60]

            try:
                api_key = keys.get("jellyfin", "")
                ms = SERVICE_MAP.get("jellyfin")
                if ms and api_key:
                    urllib.request.urlopen(urllib.request.Request(
                        f"http://{ms.host}:{ms.port}/Library/Refresh?api_key={api_key}",
                        method="POST",
                    ), timeout=5)
                    results["scan"] = {"status": "triggered", "arrs": arr_results}
                else:
                    results["scan"] = {
                        "status": "jellyfin scan skipped (no key)",
                        "arrs": arr_results,
                    }
            except Exception as exc:
                results["scan"] = {"error": str(exc)[:80], "arrs": arr_results}
        # Update Jellyfin scan interval
        scan_interval = settings.get("jellyfin_scan_interval_hours")
        if scan_interval is not None:
            results["scan_interval"] = _update_jellyfin_scan_interval(scan_interval)
        return results or {"status": "no changes"}


__all__ = ["_ContentDownloadSettingsMixin"]
