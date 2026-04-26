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

    def get_envoy_admin_summary(self) -> dict[str, Any]:
        """Operator-facing aggregate of Envoy admin-API state.

        Surfaces the data points operators ask for when triaging from
        the dashboard:
          * cluster member health (per-app reachability)
          * upstream request counts (which apps are hot)
          * upstream request-time percentiles (p50/p95/p99)
          * active connection counts (websockets / streams)
          * downstream request totals + 2xx/4xx/5xx breakdowns
          * SSL/TLS handshake error counters (cert-expiry early warning)

        Single network round-trip per data class. Returns ``{}`` for any
        block that fails — partial answers are better than no answer.
        Used by the Routing tab's "Edge gateway summary" panel; also
        useful as a programmatic feed for cluster-health checks.
        """
        envoy_admin = service_internal_url("envoy").replace(":8880", ":9901")
        out: dict[str, Any] = {
            "clusters": [],
            "request_totals": {},
            "request_p_latency_ms": {},
            "active_connections": {},
            "downstream_breakdown": {
                "total": 0, "rq_2xx": 0, "rq_4xx": 0, "rq_5xx": 0,
            },
            "tls_handshake_errors": 0,
        }
        # 1. Cluster health
        try:
            req = urllib.request.Request(f"{envoy_admin}/clusters?format=json")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
            for c in data.get("cluster_statuses", []):
                hosts = c.get("host_statuses", [])
                healthy = sum(
                    1 for h in hosts
                    if h.get("health_status", {}).get("eds_health_status")
                    in ("HEALTHY", None)
                    and not h.get("health_status", {}).get("failed_active_health_check")
                )
                out["clusters"].append({
                    "name": c.get("name", ""),
                    "hosts": len(hosts),
                    "healthy": healthy,
                    "added_via_api": c.get("added_via_api", False),
                })
        except Exception as exc:  # noqa: BLE001
            out["clusters_error"] = str(exc)[:80]

        # 2. Stats — request totals, latency percentiles, downstream counters
        try:
            req = urllib.request.Request(f"{envoy_admin}/stats?format=json")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
            stats = data.get("stats", [])
            for s in stats:
                name = str(s.get("name", ""))
                value = s.get("value")
                # Request totals per cluster (e.g. cluster.service_jellyfin.upstream_rq_total)
                if name.startswith("cluster.") and name.endswith(".upstream_rq_total"):
                    cluster = name.split(".")[1]
                    out["request_totals"][cluster] = int(value or 0)
                # Active connections per cluster
                elif name.startswith("cluster.") and name.endswith(".upstream_cx_active"):
                    cluster = name.split(".")[1]
                    out["active_connections"][cluster] = int(value or 0)
                # Downstream breakdown (gateway-level totals)
                elif name.endswith(".downstream_rq_total"):
                    out["downstream_breakdown"]["total"] = (
                        out["downstream_breakdown"]["total"] + int(value or 0)
                    )
                elif name.endswith(".downstream_rq_2xx"):
                    out["downstream_breakdown"]["rq_2xx"] = (
                        out["downstream_breakdown"]["rq_2xx"] + int(value or 0)
                    )
                elif name.endswith(".downstream_rq_4xx"):
                    out["downstream_breakdown"]["rq_4xx"] = (
                        out["downstream_breakdown"]["rq_4xx"] + int(value or 0)
                    )
                elif name.endswith(".downstream_rq_5xx"):
                    out["downstream_breakdown"]["rq_5xx"] = (
                        out["downstream_breakdown"]["rq_5xx"] + int(value or 0)
                    )
                # SSL handshake errors
                elif "ssl.handshake" in name and "error" in name:
                    out["tls_handshake_errors"] = (
                        out["tls_handshake_errors"] + int(value or 0)
                    )
            # Latency percentiles via the histogram dump
            for h in (data.get("histograms") or {}).get("computed_quantiles", []):
                name = str(h.get("name", ""))
                if not name.startswith("cluster."):
                    continue
                if not name.endswith(".upstream_rq_time"):
                    continue
                cluster = name.split(".")[1]
                quantiles = h.get("values") or []
                # Envoy returns quantiles in fixed order: 0/25/50/75/90/95/99/99.5/99.9/100.
                # We surface 50/95/99 (operator-relevant percentiles).
                if len(quantiles) >= 9:
                    out["request_p_latency_ms"][cluster] = {
                        "p50": quantiles[2].get("interval"),
                        "p95": quantiles[5].get("interval"),
                        "p99": quantiles[6].get("interval"),
                    }
        except Exception as exc:  # noqa: BLE001
            out["stats_error"] = str(exc)[:80]
        return out

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
get_envoy_admin_summary = _instance.get_envoy_admin_summary
get_rss_feed = _instance.get_rss_feed
get_grafana_dashboard = _instance.get_grafana_dashboard
