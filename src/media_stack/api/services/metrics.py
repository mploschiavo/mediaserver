"""Metrics services: Prometheus, Envoy stats, RSS feed, Grafana dashboard."""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Any

from .health import probe_services
from media_stack.api.services.registry import service_internal_url


class MetricsService:
    """Observability metrics: Prometheus, Envoy, RSS, Grafana."""

    def get_prometheus_metrics(self, cache: Any) -> str:
        """Generate Prometheus-format metrics from service health data."""
        health = probe_services(cache)
        services = health.get("services", {})
        lines = [
            "# HELP media_stack_service_up Whether a service is reachable (1=up, 0=down)",
            "# TYPE media_stack_service_up gauge",
        ]
        for name, info in sorted(services.items()):
            up = 1 if info.get("status") == "ok" else 0
            lines.append(f'media_stack_service_up{{service="{name}"}} {up}')

        lines.extend([
            "# HELP media_stack_service_latency_ms Service probe latency in milliseconds",
            "# TYPE media_stack_service_latency_ms gauge",
        ])
        for name, info in sorted(services.items()):
            ms = info.get("ms", 0)
            if ms:
                lines.append(f'media_stack_service_latency_ms{{service="{name}"}} {ms}')

        lines.extend([
            f"# HELP media_stack_healthy_total Total healthy services",
            f"# TYPE media_stack_healthy_total gauge",
            f"media_stack_healthy_total {health.get('healthy', 0)}",
            f"# HELP media_stack_total_services Total monitored services",
            f"# TYPE media_stack_total_services gauge",
            f"media_stack_total_services {health.get('total', 0)}",
        ])
        return "\n".join(lines) + "\n"

    def get_envoy_stats(self) -> dict[str, Any]:
        """Fetch Envoy proxy traffic statistics."""
        try:
            req = urllib.request.Request(service_internal_url("envoy") + "/stats?format=json")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
            stats = data.get("stats", [])
            filtered = {}
            for s in stats:
                name = s.get("name", "")
                if any(k in name for k in ("downstream_cx_total", "downstream_rq_total",
                                            "downstream_rq_2xx", "downstream_rq_4xx",
                                            "downstream_rq_5xx", "upstream_cx_total")):
                    filtered[name] = s.get("value", 0)
            return {"stats": filtered, "raw_count": len(stats)}
        except Exception as exc:
            return {"stats": {}, "error": str(exc)[:60]}

    def get_rss_feed(self, state: Any, cache: Any) -> str:
        """Generate RSS/Atom feed of action events and health changes."""
        state_dict = state.to_dict() if hasattr(state, "to_dict") else {}
        history = state_dict.get("action_history", [])
        items = []
        for a in reversed(history[-20:]):
            status = "error" if a.get("error") else "complete"
            title = f"Action: {a.get('name', '?')} — {status}"
            desc = f"Duration: {a.get('elapsed_seconds', '?')}s"
            if a.get("error"):
                desc += f"\nError: {a['error']}"
            items.append(f"""  <item>
    <title>{title}</title>
    <description><![CDATA[{desc}]]></description>
    <category>{status}</category>
  </item>""")
        cached = cache.get("health", 60)
        if cached:
            healthy = cached.get("healthy", 0)
            total = cached.get("total", 0)
            items.insert(0, f"""  <item>
    <title>Health: {healthy}/{total} services up</title>
    <description><![CDATA[Last probe results]]></description>
    <category>health</category>
  </item>""")
        channel_items = "\n".join(items)
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>Media Stack Controller</title>
  <description>Action events and health status</description>
  <link>/</link>
  <lastBuildDate>{time.strftime("%a, %d %b %Y %H:%M:%S %z")}</lastBuildDate>
{channel_items}
</channel>
</rss>"""

    def get_grafana_dashboard(self) -> dict[str, Any]:
        """Generate a Grafana dashboard JSON that queries /metrics."""
        panels = []
        y = 0
        panels.append({
            "type": "stat", "title": "Services Up", "gridPos": {"h": 4, "w": 6, "x": 0, "y": y},
            "targets": [{"expr": "media_stack_healthy_total", "legendFormat": "Healthy"}],
            "fieldConfig": {"defaults": {"thresholds": {"steps": [{"color": "red", "value": 0}, {"color": "green", "value": 14}]}}},
        })
        panels.append({
            "type": "timeseries", "title": "Service Latency", "gridPos": {"h": 8, "w": 18, "x": 6, "y": y},
            "targets": [{"expr": "media_stack_service_latency_ms", "legendFormat": "{{service}}"}],
        })
        return {
            "dashboard": {
                "title": "Media Stack", "panels": panels,
                "time": {"from": "now-6h", "to": "now"}, "refresh": "30s",
            },
            "overwrite": True,
        }


_instance = MetricsService()

# Backward compat — callers use module-level functions
get_prometheus_metrics = _instance.get_prometheus_metrics
get_envoy_stats = _instance.get_envoy_stats
get_rss_feed = _instance.get_rss_feed
get_grafana_dashboard = _instance.get_grafana_dashboard
