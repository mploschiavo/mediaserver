"""Lightweight HTTP API server for bootstrap runner telemetry and control."""

from __future__ import annotations

import json
import logging
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from .state import BootstrapState

logger = logging.getLogger("bootstrap_api")

ActionTriggerFn = Callable[[str, dict[str, Any]], None]

KNOWN_ACTIONS = frozenset({
    "bootstrap",
    "auto-indexers",
    "restart-apps",
    "sync-indexers",
    "envoy-config",
    "reconcile",
})

_DASHBOARD_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Bootstrap Service</title>
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
  button.warn{background:#f80;color:#000}
  #error{color:#f44;margin:8px 0}
  .history{font-size:0.9em;margin-top:8px}
  .history-item{padding:4px 0;border-bottom:1px solid #1a1a2e}
</style></head><body>
<h1>Bootstrap Service</h1>
<div class="card" id="status">Loading...</div>
<div class="card">
  <b>Actions</b><br>
  <button onclick="triggerAction('bootstrap')">Full Bootstrap</button>
  <button onclick="triggerAction('auto-indexers')">Auto-Add Indexers</button>
  <button onclick="triggerAction('envoy-config')">Regen Envoy Config</button>
  <button onclick="triggerAction('restart-apps')">Restart Apps</button>
  <button onclick="triggerAction('sync-indexers')">Sync Indexers</button>
  <button onclick="triggerAction('reconcile')">Reconcile</button>
  <button class="secondary" onclick="location.reload()">Refresh</button>
  <div id="error"></div>
</div>
<div class="card" id="apps" style="display:none"><b>App Status</b><div id="applist"></div></div>
<div class="card" id="history-card" style="display:none"><b>Action History</b><div id="history"></div></div>
<div class="card"><b>Raw Status</b><pre id="raw"></pre></div>
<script>
async function load(){
  try{
    const r=await fetch('/status');const d=await r.json();
    const p=d.phase;const cls=p==='complete'?'ok':p==='error'?'error':p==='running'?'running':'idle';
    let h='<div class="phase '+cls+'">'+p.toUpperCase()+'</div>';
    if(d.current_action)h+='<div class="running">Action: '+d.current_action+'</div>';
    if(d.initial_bootstrap_done)h+='<div class="ok">Initial bootstrap: done</div>';
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
        if(v.error)h+=' &mdash; '+v.error;
        h+='</div>';
      }
    }
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
        if(v.error)ah+=' &mdash; '+v.error;
        ah+='</div>';
      }
      appList.innerHTML=ah;
    }
    const hist=d.action_history||[];
    const histCard=document.getElementById('history-card');
    const histEl=document.getElementById('history');
    if(hist.length){
      histCard.style.display='block';
      let hh='';
      for(const a of hist.reverse().slice(0,20)){
        const cls=a.error?'error':'ok';
        hh+='<div class="history-item"><span class="dot '+cls+'"></span> '
          +a.name+' ('+a.elapsed_seconds+'s)';
        if(a.error)hh+=' &mdash; '+a.error;
        hh+='</div>';
      }
      histEl.innerHTML=hh;
    }
    document.getElementById('raw').textContent=JSON.stringify(d,null,2);
  }catch(e){document.getElementById('status').innerHTML='<div class="error">'+e+'</div>';}
}
async function triggerAction(name){
  document.getElementById('error').textContent='';
  try{
    const r=await fetch('/actions/'+name,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    const d=await r.json();
    if(r.status>=400)document.getElementById('error').textContent=d.error||'Failed';
    else{document.getElementById('error').textContent='Action '+name+' accepted';setTimeout(load,2000);}
  }catch(e){document.getElementById('error').textContent=e.toString();}
}
load();setInterval(load,5000);
</script></body></html>"""


class BootstrapAPIHandler(BaseHTTPRequestHandler):
    """HTTP request handler for bootstrap lifecycle and action endpoints."""

    state: BootstrapState
    action_trigger: ActionTriggerFn | None = None

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

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._json_response(200, {"status": "ok"})
        elif self.path == "/readyz":
            # Service is ready as soon as it's listening — actions are triggered via HTTP.
            self._json_response(200, {
                "status": "ready",
                "initial_bootstrap_done": self.state.initial_bootstrap_done,
                "phase": self.state.phase,
            })
        elif self.path == "/status":
            self._json_response(200, self.state.to_dict())
        elif self.path == "/apps":
            self._json_response(200, {"apps": dict(self.state.app_status)})
        elif self.path.startswith("/apps/") and self.path.count("/") == 2:
            app_name = self.path.split("/")[2]
            info = self.state.app_status.get(app_name)
            if info:
                self._json_response(200, {app_name: info})
            else:
                self._json_response(404, {"error": f"app '{app_name}' not found"})
        elif self.path == "/" or self.path == "/dashboard":
            self._html_response(200, _DASHBOARD_HTML)
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
        logger.info("Action accepted: %s (overrides=%s)", action_name, overrides)
        self.action_trigger(action_name, overrides)
        self._json_response(202, {"status": "accepted", "action": action_name, "overrides": overrides})


def start_api_server(
    state: BootstrapState,
    *,
    port: int = 9100,
    action_trigger: ActionTriggerFn | None = None,
) -> ThreadingHTTPServer:
    """Start the bootstrap API HTTP server on a daemon thread."""

    handler_class = type(
        "BoundHandler",
        (BootstrapAPIHandler,),
        {"state": state, "action_trigger": staticmethod(action_trigger) if action_trigger else None},
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
