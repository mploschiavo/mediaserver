#!/usr/bin/env python3
"""Fleet telemetry server — receives metrics from media stack clusters.

Designed for scale: 200M+ clients, daily or hourly pushes.

Architecture:
- Ingestion: append-only log files partitioned by hour (no DB writes on hot path)
- Cluster state: in-memory dict updated on each push, flushed to DB periodically
- Queries: read from materialized cluster state, not raw telemetry
- Storage: SQLite for cluster registry + daily rollup aggregates
- Raw telemetry: append to hourly JSONL files (cheap, compressible, archivable)

At 200M hourly pushes (~55K/sec):
- Each payload is ~500 bytes → ~100GB/day raw JSONL
- Cluster state dict: ~200M entries × 200 bytes = ~40GB RAM (need sharding for this)
- For local demo: works fine up to ~100K clusters on a single box

Run: python3 server.py [--port 8200] [--data-dir ./data]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import threading
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Storage: append-only JSONL + SQLite registry
# ---------------------------------------------------------------------------

class TelemetryStore:
    """Write-optimized telemetry storage.

    Hot path (ingest): append to JSONL file (no DB lock, ~100K writes/sec)
    Warm path (cluster state): in-memory dict, flushed to SQLite every 60s
    Cold path (history): read from JSONL files
    """

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._jsonl_dir = self.data_dir / "raw"
        self._jsonl_dir.mkdir(exist_ok=True)
        self._db_path = self.data_dir / "registry.db"
        self._db = self._init_db()
        # In-memory cluster state — fast reads, periodic flush
        self._clusters: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._ingest_count = 0
        self._current_jsonl: Any = None
        self._current_hour = ""
        # Load existing cluster state from DB
        self._load_clusters()
        # Start background flusher
        self._start_flusher()

    def _init_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS clusters (
                cluster_id TEXT PRIMARY KEY,
                cluster_name TEXT,
                first_seen REAL,
                last_seen REAL,
                payload TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_rollup (
                cluster_id TEXT,
                day TEXT,
                reports INTEGER DEFAULT 0,
                avg_services_healthy REAL,
                avg_storage_gb REAL,
                total_jobs_ok INTEGER DEFAULT 0,
                total_jobs_errors INTEGER DEFAULT 0,
                PRIMARY KEY (cluster_id, day)
            )
        """)
        conn.commit()
        return conn

    def _load_clusters(self) -> None:
        rows = self._db.execute(
            "SELECT cluster_id, cluster_name, first_seen, last_seen, payload FROM clusters"
        ).fetchall()
        for cid, cname, first, last, pj in rows:
            try:
                data = json.loads(pj) if pj else {}
            except Exception:
                data = {}
            self._clusters[cid] = {
                "cluster_id": cid,
                "cluster_name": cname,
                "first_seen": first,
                "last_seen": last,
                **data,
            }

    def _start_flusher(self) -> None:
        def _flush_loop():
            while True:
                time.sleep(60)
                self._flush_to_db()
        t = threading.Thread(target=_flush_loop, daemon=True, name="db-flusher")
        t.start()

    def _flush_to_db(self) -> None:
        with self._lock:
            snapshot = dict(self._clusters)
        for cid, data in snapshot.items():
            try:
                pj = json.dumps({
                    k: v for k, v in data.items()
                    if k not in ("cluster_id", "cluster_name", "first_seen", "last_seen")
                })
                self._db.execute("""
                    INSERT INTO clusters (cluster_id, cluster_name, first_seen, last_seen, payload)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(cluster_id) DO UPDATE SET
                        cluster_name=excluded.cluster_name,
                        last_seen=excluded.last_seen,
                        payload=excluded.payload
                """, (cid, data.get("cluster_name", ""), data.get("first_seen"), data.get("last_seen"), pj))
            except Exception:
                pass
        try:
            self._db.commit()
        except Exception:
            pass

    def _get_jsonl_file(self) -> Any:
        hour = time.strftime("%Y%m%d-%H")
        if hour != self._current_hour:
            if self._current_jsonl:
                self._current_jsonl.close()
            path = self._jsonl_dir / f"telemetry-{hour}.jsonl"
            self._current_jsonl = open(path, "a", encoding="utf-8")
            self._current_hour = hour
        return self._current_jsonl

    def ingest(self, payload: dict[str, Any]) -> None:
        """Hot path — must be fast. No DB writes."""
        cid = payload.get("cluster_id", "unknown")
        cname = payload.get("cluster_name", "")
        now = time.time()

        # Append to JSONL (append-only, no lock needed for single writer)
        try:
            f = self._get_jsonl_file()
            f.write(json.dumps(payload) + "\n")
            f.flush()
        except Exception:
            pass

        # Update in-memory cluster state
        with self._lock:
            if cid not in self._clusters:
                self._clusters[cid] = {"first_seen": now}
            c = self._clusters[cid]
            c.update({
                "cluster_id": cid,
                "cluster_name": cname,
                "last_seen": now,
                "controller": payload.get("controller", {}),
                "services": payload.get("services", {}),
                "jobs": payload.get("jobs", {}),
                "media": payload.get("media", {}),
            })
            self._ingest_count += 1

    def get_clusters(self) -> list[dict[str, Any]]:
        with self._lock:
            clusters = list(self._clusters.values())
        now = time.time()
        for c in clusters:
            c["age_hours"] = round((now - c.get("last_seen", now)) / 3600, 1)
        return sorted(clusters, key=lambda c: c.get("last_seen", 0), reverse=True)

    def get_cluster(self, cluster_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._clusters.get(cluster_id)

    def get_fleet_summary(self) -> dict[str, Any]:
        clusters = self.get_clusters()
        active = [c for c in clusters if c.get("age_hours", 999) < 24]
        return {
            "clusters_total": len(clusters),
            "clusters_active": len(active),
            "clusters_stale": len(clusters) - len(active),
            "services_healthy": sum(c.get("services", {}).get("healthy", 0) for c in clusters),
            "services_unhealthy": sum(c.get("services", {}).get("unhealthy", 0) for c in clusters),
            "total_storage_gb": round(sum(c.get("media", {}).get("storage_gb", 0) for c in clusters), 1),
            "total_libraries": sum(c.get("media", {}).get("libraries", 0) for c in clusters),
            "total_indexers": sum(c.get("media", {}).get("indexers", 0) for c in clusters),
            "total_livetv_tuners": sum(c.get("media", {}).get("livetv_tuners", 0) for c in clusters),
            "total_rx_gb": round(sum(c.get("network", {}).get("rx_gb", 0) for c in clusters), 1),
            "total_tx_gb": round(sum(c.get("network", {}).get("tx_gb", 0) for c in clusters), 1),
            "ingest_total": self._ingest_count,
        }


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

DASHBOARD_HTML = """<!DOCTYPE html>
<html><head><title>Fleet Telemetry</title>
<style>
*{box-sizing:border-box}
body{font-family:system-ui;background:#0f172a;color:#e2e8f0;margin:0;padding:20px}
h1{margin:0 0 16px;font-size:1.4em;color:#38bdf8}
.card{background:#1e293b;border-radius:8px;padding:16px;margin-bottom:12px}
.card h2{margin:0 0 4px;font-size:.85em;color:#94a3b8;text-transform:uppercase;letter-spacing:.05em}
.metric{font-size:2em;font-weight:700;color:#38bdf8}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:20px}
table{width:100%;border-collapse:collapse;font-size:.88em}
th,td{padding:8px 12px;text-align:left;border-bottom:1px solid #334155}
th{color:#94a3b8;font-weight:600;font-size:.8em;text-transform:uppercase}
.ok{color:#22c55e}.warn{color:#f59e0b}.err{color:#ef4444}
.stale{opacity:.4}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:.75em;font-weight:600}
.badge-ok{background:#166534;color:#86efac}.badge-warn{background:#713f12;color:#fde68a}.badge-err{background:#7f1d1d;color:#fca5a5}
</style></head><body>
<h1>&#128225; Fleet Telemetry</h1>
<div id="fleet" class="grid"></div>
<div class="card"><h2>Clusters</h2><div id="clusters">Loading...</div></div>
<script>
async function load(){
  const [fr,cr]=await Promise.all([fetch('/api/v1/fleet'),fetch('/api/v1/clusters')]);
  const fleet=await fr.json(), clusters=await cr.json();
  document.getElementById('fleet').innerHTML=
    card('Active Clusters',fleet.clusters_active+' / '+fleet.clusters_total)+
    card('Services',fleet.services_healthy+' <span class="ok">healthy</span>')+
    card('Storage',fleet.total_storage_gb+' GB')+
    card('Libraries',fleet.total_libraries)+
    card('Indexers',fleet.total_indexers)+
    card('Live TV',fleet.total_livetv_tuners+' tuners')+
    card('Network RX',fleet.total_rx_gb+' GB')+
    card('Network TX',fleet.total_tx_gb+' GB')+
    card('Ingested',fleet.ingest_total+' payloads');
  let h='<table><tr><th>Cluster</th><th>Platform</th><th>Version</th><th>Services</th><th>Jobs (24h)</th><th>Storage</th><th>Last Seen</th></tr>';
  for(const c of clusters){
    const stale=c.age_hours>24?' class="stale"':'';
    const svc=c.services||{};const jobs=c.jobs||{};const media=c.media||{};
    const svcBadge=svc.unhealthy>0?'<span class="badge badge-err">'+svc.unhealthy+' down</span>':'<span class="badge badge-ok">all ok</span>';
    h+='<tr'+stale+'><td><b>'+esc(c.cluster_name||c.cluster_id.substring(0,8))+'</b></td>';
    h+='<td>'+(c.controller?.platform||'?')+'</td>';
    h+='<td>'+(c.controller?.version||'?')+'</td>';
    h+='<td>'+svcBadge+' '+(svc.healthy||0)+'/'+(svc.total||0)+'</td>';
    h+='<td>'+(jobs.runs_24h||0)+' runs, '+(jobs.errors||0)+' err</td>';
    h+='<td>'+(media.storage_gb||0)+' GB</td>';
    h+='<td>'+(c.age_hours<1?'just now':c.age_hours<24?Math.round(c.age_hours)+'h ago':Math.round(c.age_hours/24)+'d ago')+'</td></tr>';
  }
  h+='</table>';
  if(!clusters.length)h='<p style="color:#94a3b8">No clusters reporting yet. Configure TELEMETRY_ENDPOINT on your media stack.</p>';
  document.getElementById('clusters').innerHTML=h;
}
function card(t,v){return '<div class="card"><h2>'+t+'</h2><div class="metric">'+v+'</div></div>';}
function esc(s){return String(s||'').replace(/</g,'&lt;');}
load();setInterval(load,10000);
</script></body></html>"""


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

class TelemetryHandler(BaseHTTPRequestHandler):
    store: TelemetryStore
    api_key: str = ""

    def log_message(self, *args):
        pass

    def _json(self, status: int, body: Any) -> None:
        payload = json.dumps(body, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def _html(self, html: str) -> None:
        payload = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _check_auth(self) -> bool:
        if not self.api_key:
            return True
        auth = self.headers.get("Authorization", "")
        if auth == f"Bearer {self.api_key}":
            return True
        self.send_response(401)
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            self._html(DASHBOARD_HTML)
        elif path == "/api/v1/fleet":
            self._json(200, self.store.get_fleet_summary())
        elif path == "/api/v1/clusters":
            self._json(200, self.store.get_clusters())
        elif path.startswith("/api/v1/clusters/"):
            cid = path[len("/api/v1/clusters/"):]
            c = self.store.get_cluster(cid)
            self._json(200 if c else 404, c or {"error": "not found"})
        elif path == "/healthz":
            self._json(200, {"status": "ok", "ingest_total": self.store._ingest_count})
        else:
            self._json(404, {"error": "not found"})

    # Schema v1 field order — must match client
    _SCHEMA_FIELDS = [
        "cluster_id", "cluster_name", "ts",
        "controller.version", "controller.platform", "controller.uptime_hours",
        "services.total", "services.healthy",
        "jobs.runs_24h", "jobs.ok", "jobs.errors", "jobs.avg_duration_s",
        "media.libraries", "media.livetv_tuners", "media.indexers",
        "media.storage_gb", "media.active_downloads",
        "media.torrent_rx_gb", "media.torrent_tx_gb",
        "network.rx_gb", "network.tx_gb", "network.containers",
    ]

    @classmethod
    def _from_compact(cls, arr: list) -> dict[str, Any]:
        """Reconstruct full payload from positional array."""
        result: dict[str, Any] = {}
        for i, field in enumerate(cls._SCHEMA_FIELDS):
            val = arr[i] if i < len(arr) else 0
            parts = field.split(".")
            if len(parts) == 1:
                result[parts[0]] = val
            else:
                result.setdefault(parts[0], {})[parts[1]] = val
        return result

    def do_POST(self):
        if self.path == "/api/v1/telemetry":
            if not self._check_auth():
                return
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 65536:
                self._json(400, {"error": "invalid body size"})
                return
            raw = self.rfile.read(length)
            # Decompress gzip if needed
            enc = (self.headers.get("Content-Encoding") or "").lower()
            if enc == "gzip":
                import gzip
                try:
                    raw = gzip.decompress(raw)
                except Exception:
                    self._json(400, {"error": "gzip decompress failed"})
                    return
            try:
                body = json.loads(raw)
            except Exception:
                self._json(400, {"error": "invalid JSON"})
                return
            # Handle compact array format: [schema_version, field1, field2, ...]
            if isinstance(body, list):
                schema_v = body[0] if body else 0
                if schema_v == 1 and len(body) > 1:
                    body = self._from_compact(body[1:])
                else:
                    self._json(400, {"error": f"unsupported schema version: {schema_v}"})
                    return
            if not body.get("cluster_id"):
                self._json(400, {"error": "cluster_id required"})
                return
            self.store.ingest(body)
            self._json(200, {"status": "ok"})
        else:
            self._json(404, {"error": "not found"})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", "0")
        self.end_headers()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# UDP Listener — high-throughput stateless ingest
# ---------------------------------------------------------------------------

class UDPListener:
    """Receives telemetry via UDP datagrams.

    Protocol:
      PING:<key_hash>:<cluster_id> → replies PONG (reliability probe)
      <key_hash>:<gzipped_compact_json> → ingests payload (fire-and-forget)

    Runs on HTTP port + 1.
    """

    def __init__(self, store: TelemetryStore, port: int, api_key: str = ""):
        import hashlib
        import socket
        self.store = store
        self.port = port
        self.api_key_hash = hashlib.md5((api_key or "").encode()).hexdigest()[:8]
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", port))
        self._count = 0

    def run(self) -> None:
        import gzip
        while True:
            try:
                data, addr = self.sock.recvfrom(2048)
                if not data:
                    continue

                # PING probe — reply PONG
                if data.startswith(b"PING:"):
                    parts = data.decode(errors="replace").split(":")
                    if len(parts) >= 2 and (not self.api_key_hash or parts[1] == self.api_key_hash):
                        self.sock.sendto(b"PONG", addr)
                    continue

                # Telemetry datagram: <key_hash>:<gzipped_payload>
                sep = data.find(b":")
                if sep < 1:
                    continue
                key_hash = data[:sep].decode(errors="replace")
                if self.api_key_hash and key_hash != self.api_key_hash:
                    continue  # Auth failed — drop silently

                compressed = data[sep + 1:]
                try:
                    raw = gzip.decompress(compressed)
                    body = json.loads(raw)
                except Exception:
                    continue

                # Handle compact array
                if isinstance(body, list) and body and body[0] == 1:
                    body = TelemetryHandler._from_compact(body[1:])

                if body.get("cluster_id"):
                    self.store.ingest(body)
                    self._count += 1

            except Exception:
                continue


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fleet telemetry server")
    parser.add_argument("--port", type=int, default=8200)
    parser.add_argument("--data-dir", default="./telemetry-data")
    parser.add_argument("--api-key", default=os.environ.get("TELEMETRY_API_KEY", ""))
    parser.add_argument("--no-udp", action="store_true", help="Disable UDP listener")
    args = parser.parse_args()

    store = TelemetryStore(args.data_dir)
    TelemetryHandler.store = store
    TelemetryHandler.api_key = args.api_key

    # Start UDP listener on port+1
    if not args.no_udp:
        udp = UDPListener(store, args.port + 1, args.api_key)
        udp_thread = threading.Thread(target=udp.run, daemon=True, name="udp-listener")
        udp_thread.start()
        print(f"  UDP ingest: :{args.port + 1}")
    else:
        print(f"  UDP: disabled")

    server = ThreadingHTTPServer(("0.0.0.0", args.port), TelemetryHandler)
    print(f"Fleet telemetry server on :{args.port}")
    print(f"  Dashboard: http://127.0.0.1:{args.port}/")
    print(f"  TCP ingest: POST /api/v1/telemetry")
    print(f"  Data: {args.data_dir}/")
    print(f"  Auth: {'Bearer token' if args.api_key else 'none'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nFlushing...")
        store._flush_to_db()
        print("Done.")


if __name__ == "__main__":
    main()
