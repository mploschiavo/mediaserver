"""Analytics + webhook-registration methods for ``ContentService``.

Split from ``content.py`` to keep the main ``ContentService`` class
under the 500-line god-class ratchet. Both methods are lift-and-
shift copies of the originals — same signatures, same behaviour.

The analytics aggregation reads the ``history_path`` endpoint of
every arr app and summarizes the last 100 events per service.
The webhook registration creates ``media-stack-scan`` webhooks on
Sonarr and Radarr so imports trigger a Jellyfin library refresh.
"""

from __future__ import annotations


import json
import os
import urllib.request
from typing import Any

from media_stack.core.logging_utils import log_swallowed
from .health import discover_api_keys
from media_stack.core.service_registry.registry import SERVICE_MAP, SERVICES


class _ContentAnalyticsMixin:
    """History-analytics + arr webhook helpers."""

    def get_download_analytics(self) -> dict[str, Any]:
        """Aggregate download history into analytics: counts by day, success rates, top indexers."""
        api_keys = discover_api_keys()
        apps = [(s.id, s.host, s.port, s.history_path) for s in SERVICES if s.history_path]
        all_records: list[dict[str, Any]] = []
        for svc_id, host, port, path in apps:
            key = api_keys.get(svc_id, "")
            if not key:
                continue
            try:
                # Fetch last 100 history records
                url = f"http://{host}:{port}{path}"
                if "?" in path:
                    url += "&pageSize=100"
                else:
                    url += "?pageSize=100"
                req = urllib.request.Request(url, headers={"X-Api-Key": key})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read())
                records = data.get("records", data) if isinstance(data, dict) else data
                if isinstance(records, list):
                    for r in records:
                        all_records.append({
                            "service": svc_id,
                            "title": str(r.get("sourceTitle", ""))[:60],
                            "event": str(r.get("eventType", "")),
                            "date": str(r.get("date", ""))[:10],
                            "quality": str(r.get("quality", {}).get("quality", {}).get("name", "")) if isinstance(r.get("quality"), dict) else "",
                            "indexer": str(r.get("data", {}).get("indexer", "")) if isinstance(r.get("data"), dict) else "",
                        })
            except Exception as exc:
                log_swallowed(exc)

        # Aggregate by day
        by_day: dict[str, int] = {}
        by_service: dict[str, int] = {}
        by_indexer: dict[str, int] = {}
        for r in all_records:
            day = r.get("date", "unknown")
            by_day[day] = by_day.get(day, 0) + 1
            svc = r.get("service", "?")
            by_service[svc] = by_service.get(svc, 0) + 1
            idx = r.get("indexer", "")
            if idx:
                by_indexer[idx] = by_indexer.get(idx, 0) + 1

        # Sort by day descending
        daily_trend = [{"date": d, "count": c} for d, c in sorted(by_day.items(), reverse=True)][:30]
        top_indexers = sorted(by_indexer.items(), key=lambda x: x[1], reverse=True)[:10]

        return {
            "total_records": len(all_records),
            "daily_trend": daily_trend,
            "by_service": by_service,
            "top_indexers": [{"name": n, "count": c} for n, c in top_indexers],
        }

    def ensure_arr_scan_webhooks(self, controller_url: str = "") -> dict[str, Any]:
        """Register webhooks on Sonarr/Radarr to trigger Jellyfin scan on import.

        Creates a 'media-stack-scan' webhook on each arr service that POSTs
        to /webhooks/arr on the controller when content is downloaded.
        """
        if not controller_url:
            controller_url = f"http://media-stack-controller:{os.environ.get('BOOTSTRAP_API_PORT', '9100')}"
        webhook_url = f"{controller_url}/webhooks/arr"
        webhook_name = "media-stack-scan"
        api_keys = discover_api_keys()
        results: dict[str, str] = {}

        for svc_id in ("sonarr", "radarr"):
            svc = SERVICE_MAP.get(svc_id)
            if not svc:
                continue
            key = api_keys.get(svc_id, "")
            if not key:
                results[svc_id] = "no API key"
                continue
            try:
                base = f"http://{svc.host}:{svc.port}"
                # Check existing webhooks (use core HTTP client for redirect handling)
                from media_stack.core.http import HttpClient
                _http = HttpClient()
                _, existing, _ = _http.request(base, "/api/v3/notification", api_key=key)
                already = any(n.get("name") == webhook_name for n in existing)
                if already:
                    results[svc_id] = "already registered"
                    continue
                # Create webhook
                payload = {
                    "name": webhook_name,
                    "implementation": "Webhook",
                    "configContract": "WebhookSettings",
                    "fields": [
                        {"name": "url", "value": webhook_url},
                        {"name": "method", "value": 1},  # POST
                    ],
                    "onDownload": True,
                    "onUpgrade": True,
                    "onImportComplete": True,
                    "onMovieAdded": svc_id == "radarr",
                    "onSeriesAdd": svc_id == "sonarr",
                    "onEpisodeFileDelete": svc_id == "sonarr",
                    "onMovieFileDelete": svc_id == "radarr",
                    "supportsOnDownload": True,
                    "supportsOnUpgrade": True,
                    "supportsOnImportComplete": True,
                }
                _http.request(base, "/api/v3/notification", api_key=key,
                              method="POST", payload=payload)
                results[svc_id] = "registered"
            except Exception as exc:
                results[svc_id] = f"error: {str(exc)[:60]}"
        return {"webhooks": results, "url": webhook_url}


__all__ = ["_ContentAnalyticsMixin"]
