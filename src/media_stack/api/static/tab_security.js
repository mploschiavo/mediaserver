/* Security tab — detection signals from the SecurityReportService.
 *
 * Three panels:
 *   1. Failed login clusters (credential-stuffing by IP /24)
 *   2. New-location alerts (user logged in from never-seen IP)
 *   3. Concurrent session spikes (shared-credential / ATO signal)
 *
 * Plus a per-user login-history drawer opened from any row.
 *
 * Same XSS discipline as tab_sessions.js: textContent only, no
 * innerHTML with provider data. A11y: ARIA live regions for
 * refresh announcements, semantic section landmarks, focusable
 * expand buttons. Lazy-loaded; AbortController cancels on tab switch.
 */

(function () {
  "use strict";

  const FAILED_ENDPOINT = "/api/security/failed-logins";
  const NEW_LOC_ENDPOINT = "/api/security/new-locations";
  const CONCURRENT_ENDPOINT = "/api/security/concurrent";
  const LOGIN_HISTORY_ENDPOINT = (user) =>
    `/api/users/${encodeURIComponent(user)}/login-history`;

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
      for (const c of opts.children) {
        if (c) node.appendChild(c);
      }
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

  function section(titleText, id) {
    const s = el("section", {
      className: "ms-security-panel",
      attrs: { "aria-labelledby": `${id}-heading` },
    });
    s.appendChild(el("h3", {
      text: titleText,
      attrs: { id: `${id}-heading` },
    }));
    const body = el("div", { attrs: { id: `${id}-body` } });
    s.appendChild(body);
    return { section: s, body };
  }

  function table(headers) {
    const t = el("table", {
      className: "ms-security-table",
      attrs: { role: "table" },
    });
    const thead = el("thead");
    const tr = el("tr");
    for (const h of headers) {
      tr.appendChild(el("th", { text: h, attrs: { scope: "col" } }));
    }
    thead.appendChild(tr);
    t.appendChild(thead);
    const tbody = el("tbody");
    t.appendChild(tbody);
    return { table: t, tbody };
  }

  function severityBadge(count, threshold) {
    const severity = count >= threshold * 2 ? "critical"
      : count >= threshold ? "warn" : "info";
    return el("span", {
      text: String(count),
      className: `ms-badge ms-severity-${severity}`,
      attrs: { "aria-label": `Count: ${count} — severity ${severity}` },
    });
  }

  function renderFailedLoginsRow(cluster) {
    const tr = el("tr");
    tr.appendChild(el("td", { text: cluster.ip_prefix || "" }));
    const usersTd = el("td");
    const users = (cluster.usernames || []).slice(0, 6);
    for (const u of users) {
      usersTd.appendChild(el("span", {
        text: u, className: "ms-chip ms-chip-user",
      }));
    }
    if ((cluster.usernames || []).length > 6) {
      usersTd.appendChild(el("span", {
        text: ` +${cluster.usernames.length - 6}`,
        className: "ms-chip-overflow",
      }));
    }
    tr.appendChild(usersTd);
    const cntTd = el("td");
    cntTd.appendChild(severityBadge(cluster.attempt_count || 0, 5));
    tr.appendChild(cntTd);
    tr.appendChild(el("td", { text: cluster.first_seen || "" }));
    tr.appendChild(el("td", { text: cluster.last_seen || "" }));
    return tr;
  }

  function renderNewLocationRow(alert) {
    const tr = el("tr");
    const userTd = el("td");
    const historyBtn = el("button", {
      text: alert.username || "(anonymous)",
      className: "ms-inline-link",
      attrs: {
        type: "button",
        "aria-label": `Show login history for ${alert.username}`,
      },
    });
    historyBtn.addEventListener("click", () => {
      loadLoginHistory(alert.username);
    });
    userTd.appendChild(historyBtn);
    tr.appendChild(userTd);
    tr.appendChild(el("td", { text: alert.ip_prefix || "" }));
    tr.appendChild(el("td", { text: alert.provider || "" }));
    tr.appendChild(el("td", { text: alert.observed_at || "" }));
    return tr;
  }

  function renderConcurrentRow(alert) {
    const tr = el("tr");
    tr.appendChild(el("td", { text: alert.username || "(anonymous)" }));
    const cntTd = el("td");
    cntTd.appendChild(severityBadge(alert.count || 0, alert.threshold || 5));
    tr.appendChild(cntTd);
    tr.appendChild(el("td", { text: String(alert.threshold || 5) }));
    const provTd = el("td");
    for (const p of (alert.providers || [])) {
      provTd.appendChild(el("span", {
        text: p, className: "ms-chip ms-chip-provider",
      }));
    }
    tr.appendChild(provTd);
    return tr;
  }

  function renderPanel(body, rows, columns, emptyText) {
    clearChildren(body);
    if (!rows.length) {
      body.appendChild(stateMsg(emptyText));
      return;
    }
    const { table: t, tbody } = table(columns);
    for (const r of rows) tbody.appendChild(r);
    body.appendChild(t);
  }

  async function loadLoginHistory(username) {
    const drawer = document.getElementById("ms-login-history-drawer");
    if (!drawer) return;
    clearChildren(drawer);
    drawer.hidden = false;
    drawer.appendChild(el("h4", { text: `Login history — ${username}` }));
    const closeBtn = el("button", {
      text: "Close",
      className: "ms-drawer-close",
      attrs: { type: "button" },
    });
    closeBtn.addEventListener("click", () => { drawer.hidden = true; });
    drawer.appendChild(closeBtn);
    drawer.appendChild(stateMsg("Loading..."));
    try {
      const resp = await apiFetch(LOGIN_HISTORY_ENDPOINT(username));
      if (!resp.ok) {
        clearChildren(drawer);
        drawer.appendChild(el("h4", { text: `Login history — ${username}` }));
        drawer.appendChild(closeBtn.cloneNode(true));
        drawer.appendChild(stateMsg(`HTTP ${resp.status}`, "alert"));
        return;
      }
      const data = await resp.json();
      const entries = data.entries || [];
      clearChildren(drawer);
      drawer.appendChild(el("h4", { text: `Login history — ${username}` }));
      drawer.appendChild(closeBtn);
      if (!entries.length) {
        drawer.appendChild(stateMsg("No login history."));
        return;
      }
      const { table: t, tbody } = table(["When", "Action", "IP", "Device", "Result"]);
      for (const e of entries) {
        const tr = el("tr");
        tr.appendChild(el("td", { text: e.timestamp || "" }));
        tr.appendChild(el("td", { text: e.action || "" }));
        tr.appendChild(el("td", { text: e.ip || "" }));
        tr.appendChild(el("td", {
          text: (e.detail && e.detail.device_class) || "",
        }));
        tr.appendChild(el("td", { text: e.result || "" }));
        tbody.appendChild(tr);
      }
      drawer.appendChild(t);
    } catch (err) {
      clearChildren(drawer);
      drawer.appendChild(el("h4", { text: `Login history — ${username}` }));
      drawer.appendChild(stateMsg(String(err).slice(0, 120), "alert"));
    }
  }

  async function fetchJSON(endpoint, signal) {
    const resp = await apiFetch(endpoint, { signal });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
  }

  async function loadAll(root) {
    if (_abortController) _abortController.abort();
    _abortController = new AbortController();
    clearChildren(root);

    const live = el("div", {
      attrs: {
        id: "ms-security-live-region",
        "aria-live": "polite",
        class: "ms-sr-only",
      },
    });
    root.appendChild(live);

    const failed = section("Failed login clusters", "ms-failed");
    const newLoc = section("New-location alerts", "ms-newloc");
    const conc = section("Concurrent session spikes", "ms-conc");
    root.appendChild(failed.section);
    root.appendChild(newLoc.section);
    root.appendChild(conc.section);

    const drawer = el("section", {
      className: "ms-login-history-drawer",
      attrs: {
        id: "ms-login-history-drawer",
        hidden: "hidden",
        "aria-labelledby": "ms-login-history-heading",
      },
    });
    root.appendChild(drawer);

    failed.body.appendChild(stateMsg("Loading..."));
    newLoc.body.appendChild(stateMsg("Loading..."));
    conc.body.appendChild(stateMsg("Loading..."));

    const sig = _abortController.signal;
    const jobs = await Promise.allSettled([
      fetchJSON(FAILED_ENDPOINT, sig),
      fetchJSON(NEW_LOC_ENDPOINT, sig),
      fetchJSON(CONCURRENT_ENDPOINT, sig),
    ]);

    if (jobs[0].status === "fulfilled") {
      const rows = (jobs[0].value.clusters || []).map(renderFailedLoginsRow);
      renderPanel(failed.body, rows,
        ["IP /24", "Users attempted", "Count", "First seen", "Last seen"],
        "No failed-login clusters in the last 24 hours.");
    } else if (jobs[0].reason && jobs[0].reason.name !== "AbortError") {
      clearChildren(failed.body);
      failed.body.appendChild(stateMsg(String(jobs[0].reason).slice(0, 200), "alert"));
    }
    if (jobs[1].status === "fulfilled") {
      const rows = (jobs[1].value.alerts || []).map(renderNewLocationRow);
      renderPanel(newLoc.body, rows,
        ["User", "IP /24", "Provider", "Observed at"],
        "No new-location alerts in the last 24 hours.");
    } else if (jobs[1].reason && jobs[1].reason.name !== "AbortError") {
      clearChildren(newLoc.body);
      newLoc.body.appendChild(stateMsg(String(jobs[1].reason).slice(0, 200), "alert"));
    }
    if (jobs[2].status === "fulfilled") {
      const rows = (jobs[2].value.alerts || []).map(renderConcurrentRow);
      renderPanel(conc.body, rows,
        ["User", "Count", "Threshold", "Providers"],
        "No users currently over the concurrent-session threshold.");
    } else if (jobs[2].reason && jobs[2].reason.name !== "AbortError") {
      clearChildren(conc.body);
      conc.body.appendChild(stateMsg(String(jobs[2].reason).slice(0, 200), "alert"));
    }
    _abortController = null;
  }

  window.renderSecurityTab = function (rootEl) {
    const root = rootEl || document.getElementById("tab-security");
    if (!root) return;
    loadAll(root);
  };
})();
