/* Sessions tab — aggregated view of every live session across
 * every provider (controller + Authelia + Jellyfin + ...).
 *
 * Lazy-loaded by the main dashboard when the Sessions tab becomes
 * active. Depends on two globals from dashboard.html:
 *   - apiFetch(url, opts)  — fetch with credentials + CSRF token
 *   - showTab(id, btn)     — tab switcher (not used here)
 *
 * XSS discipline (enforced by tests/unit/test_xss_safe_ui_ratchet.py):
 *   - All user/provider-controlled strings render via textContent.
 *   - DOM built via createElement + appendChild; never innerHTML.
 *   - Attribute values set via setAttribute on an allowlist of
 *     safe attributes (class, title, aria-label, type, data-*).
 *
 * A11y:
 *   - Semantic <table> with <caption>, <thead>, scoped headers.
 *   - Every icon-only button carries aria-label.
 *   - Live region announces revoke outcomes (aria-live="polite").
 *   - :focus-visible indicator is inherited from the parent CSS.
 *
 * First-class product:
 *   - Loading / empty / error states — no silent failures.
 *   - AbortController cancels in-flight fetch on tab switch.
 *   - Device-class badge rendered as icon + a11y label.
 *   - "First seen IP" flag highlighted for admin review.
 *   - Revoke is idempotent via Idempotency-Key header.
 */

(function () {
  "use strict";

  const SESSIONS_ENDPOINT = "/api/sessions/active";
  let _abortController = null;

  // Device-class → { emoji, label } — safe content, no interpolation.
  const DEVICE_ICONS = Object.freeze({
    TV: { emoji: "📺", label: "TV" },
    PHONE: { emoji: "📱", label: "Phone" },
    TABLET: { emoji: "📱", label: "Tablet" },
    DESKTOP: { emoji: "🖥️", label: "Desktop" },
    CLI: { emoji: "⌨️", label: "CLI" },
    UNKNOWN: { emoji: "❓", label: "Unknown" },
  });

  function randomIdempotencyKey() {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
  }

  function clearChildren(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function el(tag, opts) {
    const node = document.createElement(tag);
    if (!opts) return node;
    if (opts.text) node.textContent = opts.text;
    if (opts.className) node.className = opts.className;
    if (opts.attrs) {
      for (const [name, value] of Object.entries(opts.attrs)) {
        node.setAttribute(name, String(value));
      }
    }
    if (opts.children) {
      for (const child of opts.children) {
        if (child) node.appendChild(child);
      }
    }
    return node;
  }

  function renderLoading(root) {
    clearChildren(root);
    root.appendChild(el("div", {
      text: "Loading sessions...",
      className: "ms-tab-loading",
      attrs: { role: "status", "aria-live": "polite" },
    }));
  }

  function renderError(root, detail) {
    clearChildren(root);
    const box = el("div", {
      className: "ms-tab-error",
      attrs: { role: "alert" },
    });
    box.appendChild(el("strong", { text: "Could not load sessions." }));
    box.appendChild(el("div", { text: String(detail || "").slice(0, 200) }));
    root.appendChild(box);
  }

  function renderEmpty(root) {
    clearChildren(root);
    root.appendChild(el("div", {
      text: "No active sessions.",
      className: "ms-tab-empty",
      attrs: { role: "status" },
    }));
  }

  function renderProviderBadge(provider) {
    return el("span", {
      text: provider || "unknown",
      className: `ms-badge ms-badge-provider ms-badge-${provider || "unknown"}`,
      attrs: {
        "aria-label": `Provider: ${provider}`,
      },
    });
  }

  function renderDeviceBadge(deviceClass) {
    const info = DEVICE_ICONS[deviceClass] || DEVICE_ICONS.UNKNOWN;
    return el("span", {
      text: `${info.emoji} ${info.label}`,
      className: "ms-badge ms-badge-device",
      attrs: { "aria-label": `Device class: ${info.label}` },
    });
  }

  function renderFirstSeenFlag(first_seen) {
    if (!first_seen) return null;
    return el("span", {
      text: "⚠ new",
      className: "ms-flag ms-flag-first-seen",
      attrs: {
        title: "This IP has not been seen for this user in 90 days",
        "aria-label": "New location warning",
      },
    });
  }

  function revokeSession(row, username, sessionId, provider) {
    const btn = row.querySelector(".ms-revoke-btn");
    if (btn) {
      btn.disabled = true;
      btn.setAttribute("aria-busy", "true");
    }
    const announce = document.getElementById("ms-sessions-live-region");
    const path = `/api/users/${encodeURIComponent(username)}`
      + `/sessions/${encodeURIComponent(sessionId)}/revoke`;
    const opts = {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": randomIdempotencyKey(),
      },
      body: JSON.stringify({ provider }),
    };
    return apiFetch(path, opts).then(async (resp) => {
      if (!resp.ok) {
        const body = await resp.text().catch(() => "");
        if (announce) {
          announce.textContent = `Revoke failed: ${resp.status} ${body.slice(0, 80)}`;
        }
        if (btn) {
          btn.disabled = false;
          btn.setAttribute("aria-busy", "false");
        }
        return;
      }
      row.classList.add("ms-row-revoked");
      if (announce) {
        announce.textContent = `Session for ${username} on ${provider} revoked.`;
      }
      // Leave the row in place with a strike-through; the next
      // refresh removes it.
    }).catch((err) => {
      if (announce) {
        announce.textContent = `Revoke failed: ${String(err).slice(0, 120)}`;
      }
      if (btn) {
        btn.disabled = false;
        btn.setAttribute("aria-busy", "false");
      }
    });
  }

  function renderRow(session) {
    const tr = el("tr", {
      attrs: { "data-session-id": session.session_id || "" },
    });
    tr.appendChild(el("td", { text: session.username || "(anonymous)" }));
    const providerTd = el("td");
    providerTd.appendChild(renderProviderBadge(session.provider));
    tr.appendChild(providerTd);
    const deviceTd = el("td");
    deviceTd.appendChild(renderDeviceBadge(session.device_class));
    if (session.device) {
      deviceTd.appendChild(el("span", {
        text: ` ${session.device}`,
        className: "ms-device-name",
      }));
    }
    tr.appendChild(deviceTd);
    tr.appendChild(el("td", { text: session.client || "" }));
    const ipTd = el("td");
    ipTd.appendChild(el("span", { text: session.client_ip || "" }));
    const flag = renderFirstSeenFlag(session.first_seen_ip);
    if (flag) ipTd.appendChild(flag);
    tr.appendChild(ipTd);
    tr.appendChild(el("td", { text: session.connected_since || "" }));
    tr.appendChild(el("td", { text: session.last_activity || "" }));
    const actionTd = el("td");
    if (session.revokable !== false) {
      const revokeBtn = el("button", {
        text: "Revoke",
        className: "ms-revoke-btn",
        attrs: {
          type: "button",
          "aria-label": `Revoke session for ${session.username}`,
        },
      });
      revokeBtn.addEventListener("click", function () {
        revokeSession(
          tr, session.username, session.session_id, session.provider,
        );
      });
      actionTd.appendChild(revokeBtn);
    } else {
      actionTd.appendChild(el("span", {
        text: "read-only",
        className: "ms-revoke-readonly",
        attrs: { title: "Provider does not support session revoke" },
      }));
    }
    tr.appendChild(actionTd);
    return tr;
  }

  function renderTable(root, sessions) {
    clearChildren(root);
    const liveRegion = el("div", {
      attrs: {
        id: "ms-sessions-live-region",
        "aria-live": "polite",
        "aria-atomic": "true",
        role: "status",
        class: "ms-sr-only",
      },
    });
    root.appendChild(liveRegion);

    const table = el("table", {
      className: "ms-sessions-table",
      attrs: { role: "table" },
    });
    table.appendChild(el("caption", {
      text: `Active sessions (${sessions.length})`,
    }));
    const thead = el("thead");
    const headerRow = el("tr");
    const headers = [
      "User", "Provider", "Device", "Client", "IP",
      "Connected since", "Last activity", "Actions",
    ];
    for (const h of headers) {
      headerRow.appendChild(el("th", {
        text: h,
        attrs: { scope: "col" },
      }));
    }
    thead.appendChild(headerRow);
    table.appendChild(thead);
    const tbody = el("tbody");
    for (const session of sessions) {
      tbody.appendChild(renderRow(session));
    }
    table.appendChild(tbody);
    root.appendChild(table);
  }

  async function loadSessions(root) {
    if (_abortController) {
      _abortController.abort();
    }
    _abortController = new AbortController();
    renderLoading(root);
    try {
      const resp = await apiFetch(SESSIONS_ENDPOINT, {
        signal: _abortController.signal,
      });
      if (!resp.ok) {
        renderError(root, `HTTP ${resp.status}`);
        return;
      }
      const data = await resp.json();
      const sessions = Array.isArray(data.sessions) ? data.sessions
        : Array.isArray(data) ? data : [];
      if (sessions.length === 0) {
        renderEmpty(root);
        return;
      }
      renderTable(root, sessions);
    } catch (err) {
      if (err && err.name === "AbortError") return;
      renderError(root, String(err));
    } finally {
      _abortController = null;
    }
  }

  // Exposed global — called by the tab switcher when Sessions is shown.
  window.renderSessionsTab = function (rootEl) {
    const root = rootEl || document.getElementById("tab-sessions");
    if (!root) return;
    loadSessions(root);
  };
})();
