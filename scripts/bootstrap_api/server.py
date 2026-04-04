"""Lightweight HTTP API server for bootstrap runner telemetry and control."""

from __future__ import annotations

import json
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from .state import BootstrapState

RunTriggerFn = Callable[[dict[str, Any]], None]

_DASHBOARD_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Bootstrap Status</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body{font-family:system-ui,sans-serif;background:#1a1a2e;color:#e0e0e0;margin:0;padding:20px}
  h1{color:#0f9;font-size:1.5em}
  .card{background:#16213e;border-radius:8px;padding:16px;margin:12px 0}
  .phase{font-size:1.2em;font-weight:bold}
  .ok{color:#0f9}.error{color:#f44}.running{color:#ff0}.idle{color:#888}
  .preflight{display:flex;gap:8px;align-items:center;padding:4px 0}
  .dot{width:10px;height:10px;border-radius:50%;display:inline-block}
  .dot.ok{background:#0f9}.dot.error{background:#f44}
  pre{background:#0d1b2a;padding:12px;border-radius:4px;overflow-x:auto;font-size:0.85em}
  button{background:#0f9;color:#000;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;font-weight:bold;margin:4px}
  button:hover{opacity:0.8}
  button.secondary{background:#334;color:#e0e0e0}
  #error{color:#f44;margin:8px 0}
</style></head><body>
<h1>Bootstrap Runner</h1>
<div class="card" id="status">Loading...</div>
<div class="card">
  <b>Actions</b><br>
  <button onclick="triggerRun({})">Run Bootstrap</button>
  <button onclick="triggerRun({auto_download_content:true})">Enable Downloads</button>
  <button onclick="triggerRun({auto_prowlarr_indexers:true})">Auto-Add Indexers</button>
  <button onclick="triggerRun({apply_initial_preferences:true})">Apply Preferences</button>
  <button class="secondary" onclick="location.reload()">Refresh</button>
  <div id="error"></div>
</div>
<div class="card" id="apps" style="display:none"><b>App Status</b><div id="applist"></div></div>
<div class="card"><b>Raw Status</b><pre id="raw"></pre></div>
<script>
async function load(){
  try{
    const r=await fetch('/status');const d=await r.json();
    const p=d.phase;const cls=p==='complete'?'ok':p==='error'?'error':p==='running'?'running':'idle';
    let h='<div class="phase '+cls+'">'+p.toUpperCase()+'</div>';
    if(d.elapsed_seconds!=null)h+='<div>Elapsed: '+d.elapsed_seconds+'s</div>';
    if(d.error)h+='<div class="error">Error: '+d.error+'</div>';
    if(d.phases_completed&&d.phases_completed.length)
      h+='<div>Phases: '+d.phases_completed.join(', ')+'</div>';
    const pf=d.preflight_results||{};
    if(Object.keys(pf).length){
      h+='<div style="margin-top:8px"><b>Preflights</b></div>';
      for(const[k,v]of Object.entries(pf)){
        const s=v.status||'?';
        h+='<div class="preflight"><span class="dot '+(s==='ok'?'ok':'error')+'"></span>'+k+': '+s;
        if(v.error)h+=' — '+v.error;
        h+='</div>';
      }
    }
    if(d.run_overrides&&Object.keys(d.run_overrides).length)
      h+='<div style="margin-top:8px"><b>Overrides:</b> '+JSON.stringify(d.run_overrides)+'</div>';
    document.getElementById('status').innerHTML=h;
    const apps=d.app_status||{};
    const appEl=document.getElementById('apps');
    const appList=document.getElementById('applist');
    if(Object.keys(apps).length){
      appEl.style.display='block';
      let ah='';
      for(const[k,v]of Object.entries(apps)){
        const s=v.status||'?';
        ah+='<div class="preflight"><span class="dot '+(s==='ok'?'ok':'error')+'"></span>'+k+': '+s;
        if(v.error)ah+=' — '+v.error;
        ah+='</div>';
      }
      appList.innerHTML=ah;
    }
    document.getElementById('raw').textContent=JSON.stringify(d,null,2);
  }catch(e){document.getElementById('status').innerHTML='<div class="error">'+e+'</div>';}
}
async function triggerRun(overrides){
  document.getElementById('error').textContent='';
  try{
    const r=await fetch('/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(overrides)});
    const d=await r.json();
    if(r.status>=400)document.getElementById('error').textContent=d.error||'Failed';
    else{document.getElementById('error').textContent='Accepted — refreshing...';setTimeout(load,3000);}
  }catch(e){document.getElementById('error').textContent=e.toString();}
}
load();setInterval(load,5000);
</script></body></html>"""


class BootstrapAPIHandler(BaseHTTPRequestHandler):
    """HTTP request handler for bootstrap lifecycle and preflight endpoints."""

    state: BootstrapState
    run_trigger: RunTriggerFn | None = None

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass

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

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._json_response(200, {"status": "ok"})
        elif self.path == "/readyz":
            if self.state.is_complete and self.state.error is None:
                self._json_response(200, {"status": "ready"})
            else:
                self._json_response(503, {"status": self.state.phase})
        elif self.path == "/status":
            self._json_response(200, self.state.to_dict())
        elif self.path == "/" or self.path == "/dashboard":
            self._html_response(200, _DASHBOARD_HTML)
        else:
            self._json_response(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/run":
            if self.state.is_running:
                self._json_response(409, {"error": "bootstrap already running"})
            elif self.state.is_complete:
                self._json_response(409, {"error": "bootstrap already completed"})
            elif self.run_trigger is not None:
                overrides = self._read_json_body()
                self.run_trigger(overrides)
                self._json_response(202, {"status": "accepted", "overrides": overrides})
            else:
                self._json_response(503, {"error": "no run trigger configured"})
        else:
            self._json_response(404, {"error": "not found"})


def start_api_server(
    state: BootstrapState,
    *,
    port: int = 9100,
    run_trigger: RunTriggerFn | None = None,
) -> ThreadingHTTPServer:
    """Start the bootstrap API HTTP server on a daemon thread."""

    handler_class = type(
        "BoundHandler",
        (BootstrapAPIHandler,),
        {"state": state, "run_trigger": run_trigger},
    )

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
