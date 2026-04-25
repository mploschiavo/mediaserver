/* My Security tab — user self-service.
 *
 * Four sections:
 *   1. My active sessions (this session highlighted + revoke others)
 *   2. My login history (last 100 auth events)
 *   3. My API tokens (controller + provider, metadata only)
 *   4. My MFA state (enrolled? which methods? last used when?)
 *
 * "This wasn't me" button on any suspicious login row → triggers
 * revoke-everywhere + forced password rotation.
 *
 * Every action uses Idempotency-Key. Loud confirm dialog on the
 * high-impact ones. XSS-safe + a11y + async/await.
 */

(function () {
  "use strict";

  const ME_SESSIONS = "/api/me/sessions";
  const ME_TOKENS = "/api/me/tokens";
  const ME_HISTORY = "/api/me/login-history";
  const ME_MFA = "/api/me/mfa-state";
  const ME_REVOKE_OTHERS = "/api/me/revoke-others";
  const ME_THIS_WASNT_ME = "/api/me/this-wasnt-me";

  let _abortController = null;

  function clearChildren(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function el(tag, opts) {
    const node = document.createElement(tag);
    if (!opts) return node;
    if (opts.text) node.textContent = opts.text;
    if (opts.className) node.className = opts.className;
    if (opts.attrs) {
      for (const [k, v] of Object.entries(opts.attrs)) {
        node.setAttribute(k, String(v));
      }
    }
    if (opts.children) {
      for (const c of opts.children) if (c) node.appendChild(c);
    }
    return node;
  }

  function stateMsg(text, role) {
    return el("div", {
      text,
      className: "ms-tab-state",
      attrs: { role: role || "status", "aria-live": "polite" },
    });
  }

  function idempotencyKey() {
    const b = new Uint8Array(16);
    crypto.getRandomValues(b);
    return Array.from(b, x => x.toString(16).padStart(2, "0")).join("");
  }

  function announce(msg) {
    const live = document.getElementById("ms-me-live-region");
    if (live) live.textContent = msg;
  }

  function section(titleText, id) {
    const s = el("section", {
      className: "ms-me-panel",
      attrs: { "aria-labelledby": `${id}-heading` },
    });
    s.appendChild(el("h3", { text: titleText, attrs: { id: `${id}-heading` } }));
    const body = el("div", { attrs: { id: `${id}-body` } });
    s.appendChild(body);
    return { section: s, body };
  }

  function mfaBadge(state) {
    if (!state || !state.enrolled) {
      return el("span", {
        text: "⚠ Not enrolled",
        className: "ms-badge ms-severity-warn",
        attrs: { role: "status" },
      });
    }
    const methods = (state.enrolled_methods || []).join(", ");
    return el("span", {
      text: `✓ ${methods || "enrolled"}`,
      className: "ms-badge ms-severity-ok",
      attrs: {
        "aria-label": `MFA enrolled: ${methods}`,
      },
    });
  }

  function renderMFA(body, state) {
    clearChildren(body);
    const row = el("div", { className: "ms-me-mfa-row" });
    row.appendChild(mfaBadge(state));
    if (state && state.last_used_at) {
      row.appendChild(el("span", {
        text: ` — last used ${state.last_used_at} via ${state.last_used_method || "?"}`,
        className: "ms-me-mfa-last",
      }));
    }
    body.appendChild(row);
    if (state && state.required && !state.enrolled) {
      body.appendChild(el("div", {
        text: "Your role requires MFA. Enrol in Authelia ASAP.",
        className: "ms-tab-error",
        attrs: { role: "alert" },
      }));
    }
  }

  function renderTokensTable(body, tokens) {
    clearChildren(body);
    if (!tokens.length) {
      body.appendChild(stateMsg("No long-lived tokens on record."));
      return;
    }
    const t = el("table", {
      className: "ms-me-table", attrs: { role: "table" },
    });
    t.appendChild(el("caption", { text: `API tokens (${tokens.length})` }));
    const thead = el("thead");
    const headRow = el("tr");
    for (const h of ["Provider", "Name", "Created", "Last used", "Scopes"]) {
      headRow.appendChild(el("th", { text: h, attrs: { scope: "col" } }));
    }
    thead.appendChild(headRow);
    t.appendChild(thead);
    const tb = el("tbody");
    for (const tok of tokens) {
      const tr = el("tr");
      tr.appendChild(el("td", { text: tok.provider || "" }));
      tr.appendChild(el("td", { text: tok.name || "(unnamed)" }));
      tr.appendChild(el("td", { text: tok.created_at || "" }));
      tr.appendChild(el("td", { text: tok.last_used_at || "never" }));
      const scopesTd = el("td");
      for (const s of (tok.scopes || [])) {
        scopesTd.appendChild(el("span", {
          text: s, className: "ms-chip ms-chip-scope",
        }));
      }
      tr.appendChild(scopesTd);
      tb.appendChild(tr);
    }
    t.appendChild(tb);
    body.appendChild(t);
  }

  function renderHistoryTable(body, entries) {
    clearChildren(body);
    if (!entries.length) {
      body.appendChild(stateMsg("No recent logins."));
      return;
    }
    const t = el("table", {
      className: "ms-me-table", attrs: { role: "table" },
    });
    t.appendChild(el("caption", { text: `Recent logins (${entries.length})` }));
    const thead = el("thead");
    const headRow = el("tr");
    for (const h of ["When", "Action", "IP", "Device", "Result", "Actions"]) {
      headRow.appendChild(el("th", { text: h, attrs: { scope: "col" } }));
    }
    thead.appendChild(headRow);
    t.appendChild(thead);
    const tb = el("tbody");
    for (const e of entries) {
      const tr = el("tr");
      tr.appendChild(el("td", { text: e.timestamp || "" }));
      tr.appendChild(el("td", { text: e.action || "" }));
      tr.appendChild(el("td", { text: e.ip || "" }));
      tr.appendChild(el("td", {
        text: (e.detail && e.detail.device_class) || "",
      }));
      tr.appendChild(el("td", { text: e.result || "" }));
      const actTd = el("td");
      if (e.action === "login_success") {
        const btn = el("button", {
          text: "This wasn't me",
          className: "ms-danger-btn ms-small-btn",
          attrs: {
            type: "button",
            "aria-label": `Report this login as not mine: ${e.timestamp}`,
          },
        });
        btn.addEventListener("click", () => thisWasntMe(e));
        actTd.appendChild(btn);
      }
      tr.appendChild(actTd);
      tb.appendChild(tr);
    }
    t.appendChild(tb);
    body.appendChild(t);
  }

  async function thisWasntMe(entry) {
    const ok = window.confirm(
      "This will sign you out everywhere, force a password change at your next "
      + "sign-in, and alert your admin. Continue?",
    );
    if (!ok) return;
    try {
      const resp = await apiFetch(ME_THIS_WASNT_ME, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": idempotencyKey(),
        },
        body: JSON.stringify({
          login_timestamp: entry.timestamp,
          flagged_ip: entry.ip || "",
        }),
      });
      if (!resp.ok) {
        announce(`Report failed: ${resp.status}`);
        return;
      }
      announce("Reported. You'll be signed out momentarily.");
      // Let the browser clear the cookie; the redirect to login is
      // server-driven.
      setTimeout(() => window.location.reload(), 1500);
    } catch (err) {
      announce(`Report failed: ${String(err).slice(0, 120)}`);
    }
  }

  async function revokeOthers() {
    const ok = window.confirm(
      "Revoke every session except this one? You'll stay signed in here.",
    );
    if (!ok) return;
    try {
      const resp = await apiFetch(ME_REVOKE_OTHERS, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": idempotencyKey(),
        },
      });
      if (!resp.ok) {
        announce(`Revoke failed: ${resp.status}`);
        return;
      }
      announce("Other sessions revoked.");
    } catch (err) {
      announce(`Revoke failed: ${String(err).slice(0, 120)}`);
    }
  }

  function renderSessionsTable(body, payload) {
    const sessions = payload.sessions || [];
    const currentId = payload.current_session_id || "";
    clearChildren(body);
    const btnRow = el("div", { className: "ms-me-actionrow" });
    const revokeAllOthers = el("button", {
      text: "Revoke all other sessions",
      className: "ms-danger-btn",
      attrs: { type: "button", "aria-label": "Revoke all other sessions" },
    });
    revokeAllOthers.addEventListener("click", revokeOthers);
    btnRow.appendChild(revokeAllOthers);
    body.appendChild(btnRow);

    if (!sessions.length) {
      body.appendChild(stateMsg("No active sessions."));
      return;
    }
    const t = el("table", {
      className: "ms-me-table", attrs: { role: "table" },
    });
    t.appendChild(el("caption", { text: `My sessions (${sessions.length})` }));
    const thead = el("thead");
    const headRow = el("tr");
    for (const h of ["Provider", "Device", "Client", "IP", "Connected since", "Last activity", ""]) {
      headRow.appendChild(el("th", { text: h, attrs: { scope: "col" } }));
    }
    thead.appendChild(headRow);
    t.appendChild(thead);
    const tb = el("tbody");
    for (const s of sessions) {
      const tr = el("tr");
      const isCurrent = (s.session_id === currentId);
      if (isCurrent) tr.className = "ms-me-current-session";
      tr.appendChild(el("td", { text: s.provider || "" }));
      tr.appendChild(el("td", {
        text: (s.device_class || "?") + (s.device ? ` (${s.device})` : ""),
      }));
      tr.appendChild(el("td", { text: s.client || "" }));
      tr.appendChild(el("td", { text: s.client_ip || "" }));
      tr.appendChild(el("td", { text: s.connected_since || "" }));
      tr.appendChild(el("td", { text: s.last_activity || "" }));
      const actTd = el("td");
      if (isCurrent) {
        actTd.appendChild(el("span", {
          text: "this session",
          className: "ms-chip ms-chip-current",
        }));
      }
      tr.appendChild(actTd);
      tb.appendChild(tr);
    }
    t.appendChild(tb);
    body.appendChild(t);
  }

  async function load(root) {
    if (_abortController) _abortController.abort();
    _abortController = new AbortController();
    clearChildren(root);

    const live = el("div", {
      attrs: {
        id: "ms-me-live-region",
        "aria-live": "polite",
        class: "ms-sr-only",
      },
    });
    root.appendChild(live);

    const mfa = section("MFA status", "ms-me-mfa");
    const sess = section("My sessions", "ms-me-sess");
    const hist = section("Recent logins", "ms-me-hist");
    const toks = section("API tokens", "ms-me-toks");
    root.appendChild(mfa.section);
    root.appendChild(sess.section);
    root.appendChild(hist.section);
    root.appendChild(toks.section);

    mfa.body.appendChild(stateMsg("Loading..."));
    sess.body.appendChild(stateMsg("Loading..."));
    hist.body.appendChild(stateMsg("Loading..."));
    toks.body.appendChild(stateMsg("Loading..."));

    const sig = _abortController.signal;
    const [m, s, h, t] = await Promise.allSettled([
      apiFetch(ME_MFA, { signal: sig }).then(r => r.ok ? r.json() : Promise.reject(r.status)),
      apiFetch(ME_SESSIONS, { signal: sig }).then(r => r.ok ? r.json() : Promise.reject(r.status)),
      apiFetch(ME_HISTORY, { signal: sig }).then(r => r.ok ? r.json() : Promise.reject(r.status)),
      apiFetch(ME_TOKENS, { signal: sig }).then(r => r.ok ? r.json() : Promise.reject(r.status)),
    ]);
    if (m.status === "fulfilled") renderMFA(mfa.body, m.value);
    else if (m.reason && m.reason.name !== "AbortError") {
      clearChildren(mfa.body);
      mfa.body.appendChild(stateMsg(`Failed: ${String(m.reason)}`, "alert"));
    }
    if (s.status === "fulfilled") renderSessionsTable(sess.body, s.value);
    else if (s.reason && s.reason.name !== "AbortError") {
      clearChildren(sess.body);
      sess.body.appendChild(stateMsg(`Failed: ${String(s.reason)}`, "alert"));
    }
    if (h.status === "fulfilled") renderHistoryTable(hist.body, h.value.entries || []);
    else if (h.reason && h.reason.name !== "AbortError") {
      clearChildren(hist.body);
      hist.body.appendChild(stateMsg(`Failed: ${String(h.reason)}`, "alert"));
    }
    if (t.status === "fulfilled") renderTokensTable(toks.body, t.value.tokens || []);
    else if (t.reason && t.reason.name !== "AbortError") {
      clearChildren(toks.body);
      toks.body.appendChild(stateMsg(`Failed: ${String(t.reason)}`, "alert"));
    }
    _abortController = null;
  }

  window.renderMeSecurityTab = function (rootEl) {
    const root = rootEl || document.getElementById("tab-me-security");
    if (!root) return;
    load(root);
  };
})();
