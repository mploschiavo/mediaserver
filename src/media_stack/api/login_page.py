"""Styled in-page login form for the controller.

Served by ``_AuthPolicy.send_401`` when a browser hits a protected URL
without credentials. Replaces the native ``WWW-Authenticate: Basic``
popup, which is ugly and can't be styled.

The form POSTs to ``/api/auth/login`` with JSON, reads the Set-Cookie
response, then reloads into the dashboard at ``__RD__`` (substituted
with the original request path server-side).

Kept in a dedicated module so the HTML doesn't crowd server.py and so
the line-count ratchet isn't pressured.
"""

from __future__ import annotations

LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in — Media Stack</title>
<style>
/* Palette matches dashboard.html so the login → dashboard transition
   feels continuous (no color flash on redirect). */
:root{color-scheme:dark;--bg:#0f1923;--bg2:#162230;--bg3:#1e3044;--fg:#e0e0e0;--fg2:#94a3b8;--fg3:#64748b;--accent:#4ade80;--err:#f87171;--border:#1e3044}
*{box-sizing:border-box}
html,body{margin:0;padding:0;height:100%;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;background:var(--bg);color:var(--fg)}
.wrap{min-height:100%;display:flex;align-items:center;justify-content:center;padding:40px 16px}
.card{width:100%;max-width:400px;background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:32px;box-shadow:0 20px 60px rgba(0,0,0,.5)}
.brand{text-align:center;margin-bottom:24px}
.brand .dot{display:inline-block;width:10px;height:10px;border-radius:50%;background:var(--accent);margin-right:8px;vertical-align:middle}
.brand h1{display:inline-block;margin:0;font-size:1.15em;font-weight:600;vertical-align:middle;color:var(--fg)}
.brand p{color:var(--fg3);font-size:.82em;margin:6px 0 0}
form{display:flex;flex-direction:column;gap:14px}
label{display:block;color:var(--fg2);font-size:.82em;margin-bottom:6px}
input{width:100%;padding:10px 12px;font:inherit;background:var(--bg3);color:var(--fg);border:1px solid var(--border);border-radius:8px;transition:border-color .15s,box-shadow .15s}
input:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 30%,transparent)}
button{width:100%;padding:11px 14px;background:var(--accent);color:#0b1219;border:0;border-radius:8px;font:inherit;font-weight:600;cursor:pointer;transition:filter .15s}
button:hover{filter:brightness(1.08)}
button:disabled{opacity:.6;cursor:not-allowed}
.err{background:rgba(248,113,113,.12);border:1px solid var(--err);color:var(--err);border-radius:8px;padding:10px 12px;font-size:.85em;display:none}
.err.shown{display:block}
.hint{color:var(--fg3);font-size:.78em;text-align:center;margin-top:16px}
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <div class="brand"><span class="dot"></span><h1>Media Stack</h1><p>Sign in to continue</p></div>
    <div class="err" id="err"></div>
    <form id="f" autocomplete="on">
      <div><label for="u">Username or email</label><input id="u" name="username" autocomplete="username" autofocus required></div>
      <div><label for="p">Password</label><input id="p" name="password" type="password" autocomplete="current-password" required></div>
      <button id="go" type="submit">Sign in</button>
    </form>
    <div class="hint">Forgot your password? Contact your administrator.</div>
  </div>
</div>
<script>
(function(){
  var rd="__RD__";
  var f=document.getElementById("f"),b=document.getElementById("go"),e=document.getElementById("err");
  function showErr(msg){e.textContent=msg;e.classList.add("shown");}
  f.addEventListener("submit",async function(ev){
    ev.preventDefault();
    e.classList.remove("shown");
    b.disabled=true;b.textContent="Signing in\u2026";
    try{
      var res=await fetch("/api/auth/login",{method:"POST",headers:{"Content-Type":"application/json"},credentials:"same-origin",body:JSON.stringify({username:document.getElementById("u").value,password:document.getElementById("p").value})});
      if(res.ok){location.replace(rd||"/");return;}
      var j={};try{j=await res.json();}catch(e2){}
      showErr(j.error||("Sign-in failed (HTTP "+res.status+")"));
    }catch(er){showErr("Network error: "+(er&&er.message||er));}
    b.disabled=false;b.textContent="Sign in";
  });
})();
</script>
</body>
</html>
"""
