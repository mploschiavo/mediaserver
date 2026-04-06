"""Lightweight HTTP API server for bootstrap runner telemetry and control."""

from __future__ import annotations

import json
import logging
import signal
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from .state import BootstrapState

logger = logging.getLogger("controller_api")

ActionTriggerFn = Callable[[str, dict[str, Any]], None]


def _fire_webhooks(state: BootstrapState, event: str, payload: dict[str, Any]) -> None:
    """POST JSON to all registered webhook URLs (fire-and-forget)."""
    urls = list(state.webhook_urls)
    if not urls:
        return
    body = json.dumps({"event": event, **payload}, default=str).encode("utf-8")
    for url in urls:
        try:
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=8)
        except Exception as exc:
            logger.debug("Webhook delivery failed for %s: %s", url, exc)

KNOWN_ACTIONS = frozenset({
    "bootstrap",
    "auto-indexers",
    "restart-apps",
    "sync-indexers",
    "envoy-config",
    "reconcile",
})

_DASHBOARD_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Media Stack Controller</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,sans-serif;background:#0f1923;color:#e0e0e0;margin:0;padding:0}
header{background:#162230;padding:16px 24px;border-bottom:1px solid #234;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
header h1{margin:0;font-size:1.4em;color:#4ade80;display:flex;align-items:center;gap:8px}
header h1 .badge{font-size:0.55em;padding:3px 8px;border-radius:10px;font-weight:normal}
header .links{display:flex;gap:8px;flex-wrap:wrap}
header .links a{color:#94a3b8;text-decoration:none;font-size:0.85em;padding:4px 10px;border-radius:4px;border:1px solid #334}
header .links a:hover{color:#fff;border-color:#4ade80}
.container{max-width:960px;margin:0 auto;padding:16px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:700px){.grid{grid-template-columns:1fr}}
.card{background:#162230;border-radius:10px;padding:16px;border:1px solid #1e3044}
.card h2{margin:0 0 12px;font-size:1.05em;color:#94a3b8;font-weight:600}
.phase{font-size:1.3em;font-weight:bold;margin-bottom:4px}
.ok{color:#4ade80}.error{color:#f87171}.running{color:#fbbf24}.idle{color:#64748b}
.row{display:flex;gap:8px;align-items:center;padding:5px 0;border-bottom:1px solid #1e3044}
.row:last-child{border:none}
.dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.dot.ok{background:#4ade80}.dot.error{background:#f87171}.dot.warn{background:#fbbf24}.dot.idle{background:#64748b}
.actions{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
button{border:none;padding:9px 16px;border-radius:6px;cursor:pointer;font-weight:600;font-size:0.88em;transition:all .15s}
.btn-primary{background:#4ade80;color:#0f1923}
.btn-primary:hover{background:#22c55e}
.btn-secondary{background:#1e3044;color:#e0e0e0;border:1px solid #334}
.btn-secondary:hover{background:#263d56}
.btn-warn{background:#f97316;color:#fff}
.btn-warn:hover{background:#ea580c}
.btn-danger{background:#ef4444;color:#fff}
.btn-danger:hover{background:#dc2626}
.toggle{display:flex;align-items:center;gap:10px;padding:8px 0}
.switch{position:relative;width:44px;height:24px;cursor:pointer}
.switch input{opacity:0;width:0;height:0}
.slider{position:absolute;inset:0;background:#334;border-radius:12px;transition:.3s}
.slider:before{content:'';position:absolute;height:18px;width:18px;left:3px;bottom:3px;background:#94a3b8;border-radius:50%;transition:.3s}
.switch input:checked+.slider{background:#4ade80}
.switch input:checked+.slider:before{transform:translateX(20px);background:#fff}
#toast{position:fixed;bottom:20px;right:20px;background:#162230;color:#4ade80;padding:12px 20px;border-radius:8px;border:1px solid #4ade80;display:none;z-index:99;font-size:0.9em}
#toast.err{color:#f87171;border-color:#f87171}
#logs{background:#0b1219;border-radius:6px;padding:10px;max-height:300px;overflow-y:auto;font-family:'Fira Code',monospace,monospace;font-size:0.78em;line-height:1.6;white-space:pre-wrap;word-break:break-word}
.log-line{color:#94a3b8}.log-line .ts{color:#64748b}.log-line .ok{color:#4ade80}.log-line .err{color:#f87171}.log-line .warn{color:#fbbf24}
details{margin-top:8px}
details summary{cursor:pointer;color:#94a3b8;font-size:0.9em}
</style></head><body>
<header>
  <h1>Media Stack Controller <span class="badge idle" id="hbadge">...</span></h1>
  <div class="links">
    <a href="/api/docs">API Docs</a>
    <a href="/logs/stream" target="_blank">SSE Stream</a>
    <a href="/status" target="_blank">Raw JSON</a>
  </div>
</header>
<div class="container">
<!-- Status -->
<div class="card" id="status-card"><div class="phase idle">Loading...</div></div>

<!-- Quick Actions -->
<div class="card">
  <h2>Quick Actions</h2>
  <div class="toggle">
    <label class="switch"><input type="checkbox" id="autoToggle" onchange="toggleAuto(this.checked)"><span class="slider"></span></label>
    <span id="autoLabel">Auto-Downloads: <b>off</b></span>
  </div>
  <div class="actions">
    <button class="btn-primary" onclick="act('bootstrap')">Configure All Apps</button>
    <button class="btn-secondary" onclick="act('auto-indexers')">Discover Indexers</button>
    <button class="btn-secondary" onclick="act('envoy-config')">Rebuild Routing</button>
    <button class="btn-secondary" onclick="act('restart-apps')">Restart All Apps</button>
    <button class="btn-secondary" onclick="act('sync-indexers')">Sync Indexers</button>
    <button class="btn-secondary" onclick="act('reconcile')">Reconcile</button>
  </div>
</div>

<!-- Health -->
<div class="grid">
<div class="card">
  <h2>Service Health</h2>
  <div id="health">Checking...</div>
</div>
<div class="card">
  <h2>API Credentials</h2>
  <div id="creds">Checking...</div>
</div>
</div>

<!-- Live Logs -->
<div class="card">
  <h2>Live Activity <button class="btn-secondary" style="float:right;padding:4px 10px;font-size:0.8em" onclick="downloadLogs()">Download Logs</button></h2>
  <div id="logs"></div>
</div>

<!-- Service Links -->
<div class="card" id="links-card" style="display:none">
  <h2>Service Links</h2>
  <div id="svc-links" class="grid"></div>
</div>

<!-- History -->
<div class="card" id="hist-card" style="display:none">
  <h2>Action History</h2>
  <div id="hist"></div>
</div>

<!-- Raw -->
<details><summary>Raw Status JSON</summary><pre id="raw" style="background:#0b1219;padding:12px;border-radius:6px;font-size:0.8em;overflow-x:auto"></pre></details>
</div>
<div id="toast"></div>
<script>
let logBuf=[];let logSeq=0;let evtSource=null;
const SVC_PORTS={jellyfin:8096,jellyseerr:5055,sonarr:8989,radarr:7878,lidarr:8686,readarr:8787,
  prowlarr:9696,qbittorrent:8080,sabnzbd:8080,bazarr:6767,maintainerr:6246,tautulli:8181,
  homepage:3000,envoy:10000,plex:32400,flaresolverr:8191};

function toast(msg,err){
  const t=document.getElementById('toast');t.textContent=msg;
  t.className=err?'err':'';t.style.display='block';
  setTimeout(()=>t.style.display='none',4000);
}

async function act(name){
  try{
    const r=await fetch('/actions/'+name,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    const d=await r.json();
    if(r.status>=400)toast(d.error||'Failed',true);
    else{toast(name+' started');setTimeout(load,1500);}
  }catch(e){toast(e.toString(),true);}
}

async function toggleAuto(on){
  try{
    await fetch('/config',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({auto_download_content:on})});
    document.getElementById('autoLabel').innerHTML='Auto-Downloads: <b>'+(on?'on':'off')+'</b>';
    toast('Auto-downloads '+(on?'enabled':'disabled'));
  }catch(e){toast(e.toString(),true);}
}

async function load(){
  try{
    const r=await fetch('/status');const d=await r.json();
    const p=d.phase;
    const cls=p==='complete'?'ok':p==='error'?'error':p==='running'?'running':'idle';
    const badge=document.getElementById('hbadge');
    badge.textContent=p.toUpperCase();badge.className='badge '+cls;

    let h='<div class="phase '+cls+'">'+p.charAt(0).toUpperCase()+p.slice(1)+'</div>';
    if(d.current_action){
      const a=d.current_action;
      h+='<div class="running">Running: '+a.name+(a.elapsed_seconds?' ('+a.elapsed_seconds+'s)':'')+'</div>';
    }
    if(d.error)h+='<div class="error">'+d.error+'</div>';
    if(d.initial_bootstrap_done)h+='<div class="ok" style="font-size:0.9em">Initial setup complete</div>';
    document.getElementById('status-card').innerHTML=h;

    // Auto-download toggle
    const cfg=d.runtime_config||{};
    const autoOn=cfg.auto_download_content===true||cfg.auto_download_content==='1';
    document.getElementById('autoToggle').checked=autoOn;
    document.getElementById('autoLabel').innerHTML='Auto-Downloads: <b>'+(autoOn?'on':'off')+'</b>';

    // Health: preflights
    const pf=d.preflight_results||{};
    let hh='';
    for(const[k,v]of Object.entries(pf)){
      const s=v.status||'unknown';
      hh+='<div class="row"><span class="dot '+(s==='ok'?'ok':'error')+'"></span><span>'+
        k.replace(/_/g,' ')+'</span></div>';
    }
    if(!hh)hh='<div style="color:#64748b">No health data yet &mdash; run Configure All Apps</div>';
    document.getElementById('health').innerHTML=hh;

    // Credentials
    let ch='';const keys=['JELLYFIN_API_KEY','SONARR_API_KEY','RADARR_API_KEY','LIDARR_API_KEY',
      'READARR_API_KEY','PROWLARR_API_KEY','BAZARR_API_KEY','SABNZBD_API_KEY','JELLYSEERR_API_KEY'];
    const allKeys={};
    for(const sec of Object.values(pf)){
      if(typeof sec==='object')for(const[k,v]of Object.entries(sec)){
        if(k.endsWith('_API_KEY')&&v)allKeys[k]=true;
      }
    }
    for(const k of keys){
      const ok=!!allKeys[k];
      ch+='<div class="row"><span class="dot '+(ok?'ok':'warn')+'"></span><span>'+
        k.replace(/_API_KEY/,'').replace(/_/g,' ')+'</span></div>';
    }
    document.getElementById('creds').innerHTML=ch;

    // History
    const hist=(d.action_history||[]).slice().reverse().slice(0,10);
    if(hist.length){
      document.getElementById('hist-card').style.display='block';
      let hx='';
      for(const a of hist){
        const c=a.error?'error':'ok';
        hx+='<div class="row"><span class="dot '+c+'"></span><span>'+a.name+
          ' <span style="color:#64748b">'+(a.elapsed_seconds||'?')+'s</span>'+
          (a.error?' &mdash; <span class="error">'+a.error+'</span>':'')+'</span></div>';
      }
      document.getElementById('hist').innerHTML=hx;
    }
    document.getElementById('raw').textContent=JSON.stringify(d,null,2);
  }catch(e){document.getElementById('status-card').innerHTML='<div class="error">'+e+'</div>';}
}

function startSSE(){
  if(evtSource)evtSource.close();
  evtSource=new EventSource('/logs/stream?after_seq='+logSeq);
  evtSource.onmessage=function(e){
    try{
      const d=JSON.parse(e.data);logSeq=d.seq;
      const msg=d.msg||'';
      let cls='';
      if(msg.includes('[OK]')||msg.includes('complete'))cls=' ok';
      else if(msg.includes('[ERR]')||msg.includes('failed'))cls=' err';
      else if(msg.includes('[WARN]'))cls=' warn';
      const line='<div class="log-line"><span class="ts">'+d.ts+'</span> <span class="'+cls+'">'+
        msg.replace(/</g,'&lt;')+'</span></div>';
      logBuf.push(line);
      if(logBuf.length>500)logBuf=logBuf.slice(-300);
      const el=document.getElementById('logs');
      el.innerHTML=logBuf.join('');
      el.scrollTop=el.scrollHeight;
    }catch(ex){}
  };
  evtSource.onerror=function(){setTimeout(startSSE,3000);};
}

function downloadLogs(){
  const lines=logBuf.map(l=>l.replace(/<[^>]*>/g,'')).join('\\n');
  const blob=new Blob([lines],{type:'text/plain'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);
  a.download='media-stack-logs-'+new Date().toISOString().slice(0,19)+'.txt';
  a.click();URL.revokeObjectURL(a.href);
}

load();setInterval(load,4000);startSSE();
</script></body></html>"""


_API_DOCS_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Media Stack Controller — API Reference</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{box-sizing:border-box}
body{font-family:system-ui,sans-serif;background:#0f1923;color:#e0e0e0;margin:0;padding:0}
header{background:#162230;padding:16px 24px;border-bottom:1px solid #234}
header h1{margin:0;font-size:1.3em;color:#4ade80}
header a{color:#94a3b8;text-decoration:none;font-size:0.9em}
.container{max-width:900px;margin:0 auto;padding:20px}
.endpoint{background:#162230;border:1px solid #1e3044;border-radius:10px;padding:16px;margin:16px 0}
.method{display:inline-block;padding:3px 10px;border-radius:4px;font-weight:bold;font-size:0.85em;margin-right:8px}
.GET{background:#3b82f6;color:#fff}.POST{background:#4ade80;color:#000}.DELETE{background:#ef4444;color:#fff}
.path{font-family:monospace;font-size:1.05em;color:#fff}
.desc{color:#94a3b8;margin:8px 0 0;font-size:0.92em}
pre{background:#0b1219;padding:12px;border-radius:6px;font-size:0.82em;overflow-x:auto;margin:8px 0}
code{color:#fbbf24}
h2{color:#94a3b8;font-size:1.1em;margin:24px 0 8px;padding-top:16px;border-top:1px solid #1e3044}
</style></head><body>
<header><h1>Media Stack Controller &mdash; API Reference</h1>
<a href="/">&larr; Back to Dashboard</a></header>
<div class="container">

<h2>Health &amp; Status</h2>

<div class="endpoint">
<span class="method GET">GET</span><span class="path">/healthz</span>
<div class="desc">Liveness probe. Always returns 200 if the service is running.</div>
<pre>curl http://localhost:9100/healthz
<code>{"status": "ok"}</code></pre></div>

<div class="endpoint">
<span class="method GET">GET</span><span class="path">/readyz</span>
<div class="desc">Readiness probe. Returns initial bootstrap status.</div>
<pre>curl http://localhost:9100/readyz
<code>{"status": "ready", "initial_bootstrap_done": true, "phase": "complete"}</code></pre></div>

<div class="endpoint">
<span class="method GET">GET</span><span class="path">/status</span>
<div class="desc">Full controller state: phase, preflight results, app status, action history, runtime config.</div>
<pre>curl http://localhost:9100/status</pre></div>

<div class="endpoint">
<span class="method GET">GET</span><span class="path">/apps</span>
<div class="desc">App-level status for all configured services.</div>
<pre>curl http://localhost:9100/apps</pre></div>

<div class="endpoint">
<span class="method GET">GET</span><span class="path">/apps/{name}</span>
<div class="desc">Status for a specific app (e.g. <code>sonarr</code>, <code>jellyfin</code>).</div>
<pre>curl http://localhost:9100/apps/sonarr</pre></div>

<h2>Actions</h2>

<div class="endpoint">
<span class="method POST">POST</span><span class="path">/actions/bootstrap</span>
<div class="desc"><b>Configure All Apps.</b> Runs full pipeline: preflight checks, app wiring, post-bootstrap. Idempotent.</div>
<pre>curl -X POST http://localhost:9100/actions/bootstrap \\
  -H "Content-Type: application/json" -d '{}'

# With retry on failure:
curl -X POST http://localhost:9100/actions/bootstrap \\
  -H "Content-Type: application/json" -d '{"retry": 2}'</pre></div>

<div class="endpoint">
<span class="method POST">POST</span><span class="path">/actions/auto-indexers</span>
<div class="desc"><b>Discover Indexers.</b> Scans and tests Prowlarr indexer presets, adds working ones.</div>
<pre>curl -X POST http://localhost:9100/actions/auto-indexers \\
  -H "Content-Type: application/json" -d '{}'</pre></div>

<div class="endpoint">
<span class="method POST">POST</span><span class="path">/actions/envoy-config</span>
<div class="desc"><b>Rebuild Routing.</b> Regenerates Envoy gateway config from profile and service discovery.</div>
<pre>curl -X POST http://localhost:9100/actions/envoy-config \\
  -H "Content-Type: application/json" -d '{}'</pre></div>

<div class="endpoint">
<span class="method POST">POST</span><span class="path">/actions/restart-apps</span>
<div class="desc"><b>Restart All Apps.</b> Rolling restart of all managed services to pick up config changes.</div>
<pre>curl -X POST http://localhost:9100/actions/restart-apps \\
  -H "Content-Type: application/json" -d '{}'</pre></div>

<div class="endpoint">
<span class="method POST">POST</span><span class="path">/actions/sync-indexers</span>
<div class="desc"><b>Sync Indexers.</b> Triggers Prowlarr ApplicationIndexerSync to push indexers to Arr apps.</div>
<pre>curl -X POST http://localhost:9100/actions/sync-indexers \\
  -H "Content-Type: application/json" -d '{}'</pre></div>

<div class="endpoint">
<span class="method POST">POST</span><span class="path">/actions/reconcile</span>
<div class="desc"><b>Reconcile.</b> Re-runs bootstrap to fix drift (same as bootstrap but intended for periodic use).</div>
<pre>curl -X POST http://localhost:9100/actions/reconcile \\
  -H "Content-Type: application/json" -d '{}'</pre></div>

<h2>Configuration</h2>

<div class="endpoint">
<span class="method GET">GET</span><span class="path">/config</span>
<div class="desc">Current runtime config overrides.</div>
<pre>curl http://localhost:9100/config
<code>{"config": {"auto_download_content": false}}</code></pre></div>

<div class="endpoint">
<span class="method POST">POST</span><span class="path">/config</span>
<div class="desc">Update runtime config. Changes take effect on next action run.</div>
<pre># Enable auto-downloads:
curl -X POST http://localhost:9100/config \\
  -H "Content-Type: application/json" \\
  -d '{"auto_download_content": true}'

# Disable auto-downloads:
curl -X POST http://localhost:9100/config \\
  -H "Content-Type: application/json" \\
  -d '{"auto_download_content": false}'</pre></div>

<div class="endpoint">
<span class="method POST">POST</span><span class="path">/reload</span>
<div class="desc">Reload bootstrap profile YAML and re-apply environment config.</div>
<pre>curl -X POST http://localhost:9100/reload</pre></div>

<h2>Logs &amp; Monitoring</h2>

<div class="endpoint">
<span class="method GET">GET</span><span class="path">/logs/stream</span>
<div class="desc">Server-Sent Events (SSE) stream of real-time log lines. Connect from browser or CLI.</div>
<pre># Browser: open http://localhost:9100/logs/stream
# CLI:
curl -N http://localhost:9100/logs/stream

# Resume from a specific sequence number:
curl -N "http://localhost:9100/logs/stream?after_seq=42"</pre></div>

<h2>Webhooks</h2>

<div class="endpoint">
<span class="method GET">GET</span><span class="path">/webhooks</span>
<div class="desc">List registered webhook URLs.</div>
<pre>curl http://localhost:9100/webhooks</pre></div>

<div class="endpoint">
<span class="method POST">POST</span><span class="path">/webhooks</span>
<div class="desc">Register a webhook URL. Receives JSON POST on action completion/error.</div>
<pre>curl -X POST http://localhost:9100/webhooks \\
  -H "Content-Type: application/json" \\
  -d '{"url": "https://hooks.example.com/media-stack"}'</pre></div>

<div class="endpoint">
<span class="method DELETE">DELETE</span><span class="path">/webhooks</span>
<div class="desc">Remove a registered webhook URL.</div>
<pre>curl -X DELETE http://localhost:9100/webhooks \\
  -H "Content-Type: application/json" \\
  -d '{"url": "https://hooks.example.com/media-stack"}'</pre></div>

<h2>Lifecycle</h2>

<div class="endpoint">
<span class="method POST">POST</span><span class="path">/reset</span>
<div class="desc">Reset controller state to idle (only when no action is running).</div>
<pre>curl -X POST http://localhost:9100/reset</pre></div>

<div class="endpoint">
<span class="method POST">POST</span><span class="path">/cancel</span>
<div class="desc">Cancel the currently running action.</div>
<pre>curl -X POST http://localhost:9100/cancel</pre></div>

</div></body></html>"""


class BootstrapAPIHandler(BaseHTTPRequestHandler):
    """HTTP request handler for bootstrap lifecycle and action endpoints."""

    state: BootstrapState
    action_trigger: ActionTriggerFn | None = None
    reload_config: Callable[[], None] | None = None

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        logger.debug("[%s] %s %s", ts, self.command, self.path)

    def _json_response(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _html_response(self, status: int, html: str) -> None:
        payload = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def _sse_response(self) -> None:
        """Send Server-Sent Events stream of log lines."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        # Parse ?after_seq=N from query string.
        after_seq = 0
        if "?" in self.path:
            qs = self.path.split("?", 1)[1]
            for part in qs.split("&"):
                if part.startswith("after_seq="):
                    try:
                        after_seq = int(part.split("=", 1)[1])
                    except ValueError:
                        pass

        try:
            while True:
                entries = self.state.get_logs_since(after_seq)
                for seq, ts, msg in entries:
                    ts_str = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))
                    data = json.dumps({"seq": seq, "ts": ts_str, "msg": msg})
                    self.wfile.write(f"id: {seq}\ndata: {data}\n\n".encode())
                    after_seq = seq
                self.wfile.flush()
                # Block until next log line or timeout (long-poll).
                self.state.wait_for_log(timeout=30.0)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]  # strip query string for routing

        if path == "/healthz":
            self._json_response(200, {"status": "ok"})
        elif path == "/readyz":
            self._json_response(200, {
                "status": "ready",
                "initial_bootstrap_done": self.state.initial_bootstrap_done,
                "phase": self.state.phase,
            })
        elif path == "/status":
            self._json_response(200, self.state.to_dict())
        elif path == "/apps":
            self._json_response(200, {"apps": dict(self.state.app_status)})
        elif path.startswith("/apps/") and path.count("/") == 2:
            app_name = path.split("/")[2]
            info = self.state.app_status.get(app_name)
            if info:
                self._json_response(200, {app_name: info})
            else:
                self._json_response(404, {"error": f"app '{app_name}' not found"})
        elif path == "/config":
            self._json_response(200, {"config": dict(self.state.runtime_config)})
        elif path == "/webhooks":
            self._json_response(200, {"webhook_urls": list(self.state.webhook_urls)})
        elif path == "/logs/stream":
            self._sse_response()
        elif path == "/" or path == "/dashboard":
            self._html_response(200, _DASHBOARD_HTML)
        elif path == "/api/docs":
            self._html_response(200, _API_DOCS_HTML)
        else:
            self._json_response(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        # POST /run — backward-compatible alias for /actions/bootstrap
        if self.path == "/run":
            self._handle_action("bootstrap")
            return

        # POST /actions/{name}
        if self.path.startswith("/actions/"):
            action_name = self.path[len("/actions/"):]
            if action_name not in KNOWN_ACTIONS:
                self._json_response(
                    404, {"error": f"unknown action '{action_name}'", "known": sorted(KNOWN_ACTIONS)}
                )
                return
            self._handle_action(action_name)
            return

        # POST /config — update runtime config toggles
        if self.path == "/config":
            body = self._read_json_body()
            if not body:
                self._json_response(400, {"error": "JSON body required"})
                return
            updated = self.state.update_config(body)
            logger.info("Config updated: %s", body)
            self._json_response(200, {"status": "updated", "config": updated})
            return

        # POST /webhooks — register a webhook URL
        if self.path == "/webhooks":
            body = self._read_json_body()
            url = str(body.get("url", "")).strip()
            if not url:
                self._json_response(400, {"error": "url field required"})
                return
            if url not in self.state.webhook_urls:
                self.state.webhook_urls.append(url)
            logger.info("Webhook registered: %s", url)
            self._json_response(200, {"status": "registered", "webhook_urls": list(self.state.webhook_urls)})
            return

        # POST /reload — reload profile and apply config policy
        if self.path == "/reload":
            if self.reload_config is not None:
                try:
                    self.reload_config()
                    self._json_response(200, {"status": "reloaded"})
                except Exception as exc:
                    self._json_response(500, {"error": f"reload failed: {exc}"})
            else:
                self._json_response(503, {"error": "no reload handler configured"})
            return

        # POST /reset — reset state for re-run
        if self.path == "/reset":
            if self.state.action_running:
                self._json_response(409, {"error": "cannot reset while action is running"})
            else:
                logger.info("State reset requested")
                self.state.phase = "idle"
                self.state.error = None
                self._json_response(200, {"status": "reset"})
            return

        # POST /cancel — cancel the current action
        if self.path == "/cancel":
            if self.state.cancel_action():
                self._json_response(
                    200,
                    {
                        "status": "cancel_requested",
                        "action": self.state.current_action.name
                        if self.state.current_action
                        else None,
                    },
                )
            else:
                self._json_response(409, {"error": "no action running to cancel"})
            return

        self._json_response(404, {"error": "not found"})

    def do_DELETE(self) -> None:  # noqa: N802
        # DELETE /actions/{id} — cancel a specific action by id
        if self.path.startswith("/actions/"):
            action_id = self.path[len("/actions/"):]
            action = self.state.get_action(action_id)
            if not action:
                self._json_response(404, {"error": f"action '{action_id}' not found"})
            elif action.is_terminal:
                self._json_response(409, {"error": "action already completed", "status": action.status.value})
            else:
                self.state.cancel_action()
                self._json_response(200, {"status": "cancel_requested", "action": action.to_dict()})
            return
        # DELETE /webhooks — remove a webhook URL (pass {"url": "..."} in body)
        if self.path == "/webhooks":
            body = self._read_json_body()
            url = str(body.get("url", "")).strip()
            if url in self.state.webhook_urls:
                self.state.webhook_urls.remove(url)
                self._json_response(200, {"status": "removed", "webhook_urls": list(self.state.webhook_urls)})
            else:
                self._json_response(404, {"error": "webhook URL not found"})
            return
        self._json_response(404, {"error": "not found"})

    def _handle_action(self, action_name: str) -> None:
        if self.state.action_running:
            current = self.state.current_action
            logger.warning("Action rejected: %s in progress", current.name if current else "unknown")
            self._json_response(
                409,
                {
                    "error": "action already in progress",
                    "current_action": current.to_dict() if current else None,
                },
            )
            return
        if self.action_trigger is None:
            self._json_response(503, {"error": "no action trigger configured"})
            return
        overrides = self._read_json_body()
        # Merge runtime_config as defaults (explicit overrides take precedence).
        merged = {**self.state.runtime_config, **overrides}
        logger.info("Action accepted: %s (overrides=%s)", action_name, merged)
        self.action_trigger(action_name, merged)
        self._json_response(202, {"status": "accepted", "action": action_name, "overrides": merged})


ReloadConfigFn = Callable[[], None]


def start_api_server(
    state: BootstrapState,
    *,
    port: int = 9100,
    action_trigger: ActionTriggerFn | None = None,
    reload_config: ReloadConfigFn | None = None,
) -> ThreadingHTTPServer:
    """Start the bootstrap API HTTP server on a daemon thread."""

    attrs: dict[str, Any] = {"state": state}
    if action_trigger:
        attrs["action_trigger"] = staticmethod(action_trigger)
    if reload_config:
        attrs["reload_config"] = staticmethod(reload_config)
    handler_class = type("BoundHandler", (BootstrapAPIHandler,), attrs)

    server = ThreadingHTTPServer(("0.0.0.0", port), handler_class)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    original_handler = signal.getsignal(signal.SIGTERM)

    def _shutdown(signum: int, frame: Any) -> None:
        server.shutdown()
        if callable(original_handler) and original_handler not in (
            signal.SIG_DFL,
            signal.SIG_IGN,
        ):
            original_handler(signum, frame)

    signal.signal(signal.SIGTERM, _shutdown)

    return server
