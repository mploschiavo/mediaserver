/* Bans tab — user + IP ban management.
 *
 * Two tables + two add-forms:
 *   - User bans: username, reason template, reason detail, expires, actor.
 *   - IP bans:   CIDR, reason template, expires, actor.
 *
 * Reason templates are enumerated from BanReason; operators pick from
 * a <select>, free-text only permitted for "OTHER".
 *
 * Same XSS discipline + a11y as the other tabs.
 * Every mutating action uses Idempotency-Key so double-click is a
 * single server-side ban.
 */

(function () {
  "use strict";

  const USER_BANS = "/api/bans/users";
  const IP_BANS = "/api/bans/ips";

  const BAN_REASONS = Object.freeze([
    { value: "CREDENTIAL_STUFFING", label: "Credential stuffing" },
    { value: "UNAUTHORIZED_SHARING", label: "Unauthorised sharing" },
    { value: "ADMIN_REQUEST", label: "Admin request" },
    { value: "INVESTIGATION_HOLD", label: "Investigation hold" },
    { value: "SECURITY_INCIDENT", label: "Security incident" },
    { value: "POLICY_VIOLATION", label: "Policy violation" },
    { value: "OTHER", label: "Other (free-text)" },
  ]);

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

  function reasonSelect(id) {
    const sel = el("select", {
      className: "ms-ban-reason",
      attrs: {
        id,
        name: "reason",
        "aria-label": "Reason template",
        required: "required",
      },
    });
    for (const r of BAN_REASONS) {
      const opt = el("option", {
        text: r.label, attrs: { value: r.value },
      });
      sel.appendChild(opt);
    }
    return sel;
  }

  function announce(msg) {
    const live = document.getElementById("ms-bans-live-region");
    if (live) live.textContent = msg;
  }

  async function postBan(url, payload) {
    return apiFetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey(),
      },
      body: JSON.stringify(payload),
    });
  }

  async function removeBan(url) {
    return apiFetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey(),
      },
      body: JSON.stringify({ confirm: true }),
    });
  }

  function buildUserBanForm(onSuccess) {
    const form = el("form", {
      className: "ms-ban-form",
      attrs: { "aria-label": "Add user ban" },
    });

    const userInput = el("input", {
      attrs: {
        type: "text", name: "username", required: "required",
        placeholder: "username", "aria-label": "Username to ban",
      },
    });
    const reasonSel = reasonSelect("ms-user-ban-reason");
    const detailInput = el("input", {
      attrs: {
        type: "text", name: "reason_detail",
        placeholder: "Optional clarification",
        "aria-label": "Reason detail",
      },
    });
    const expiresInput = el("input", {
      attrs: {
        type: "datetime-local", name: "expires_at",
        "aria-label": "Expires at (leave blank for indefinite)",
      },
    });
    const submit = el("button", {
      text: "Ban user",
      className: "ms-danger-btn",
      attrs: { type: "submit" },
    });

    form.appendChild(userInput);
    form.appendChild(reasonSel);
    form.appendChild(detailInput);
    form.appendChild(expiresInput);
    form.appendChild(submit);

    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      submit.disabled = true;
      try {
        const resp = await postBan(USER_BANS, {
          username: userInput.value.trim(),
          reason: reasonSel.value,
          reason_detail: detailInput.value.trim(),
          expires_at: expiresInput.value
            ? new Date(expiresInput.value).toISOString() : "",
        });
        if (!resp.ok) {
          const body = await resp.text().catch(() => "");
          announce(`Ban failed: ${resp.status} ${body.slice(0, 80)}`);
          return;
        }
        announce(`${userInput.value.trim()} banned.`);
        userInput.value = ""; detailInput.value = ""; expiresInput.value = "";
        if (onSuccess) onSuccess();
      } catch (err) {
        announce(`Ban failed: ${String(err).slice(0, 120)}`);
      } finally {
        submit.disabled = false;
      }
    });
    return form;
  }

  function buildIpBanForm(onSuccess) {
    const form = el("form", {
      className: "ms-ban-form",
      attrs: { "aria-label": "Add IP ban" },
    });
    const cidrInput = el("input", {
      attrs: {
        type: "text", name: "cidr", required: "required",
        placeholder: "203.0.113.45 or 203.0.113.0/24",
        "aria-label": "CIDR or IP address",
      },
    });
    const reasonSel = reasonSelect("ms-ip-ban-reason");
    const expiresInput = el("input", {
      attrs: {
        type: "datetime-local", name: "expires_at",
        "aria-label": "Expires at",
      },
    });
    const submit = el("button", {
      text: "Ban IP",
      className: "ms-danger-btn",
      attrs: { type: "submit" },
    });
    form.appendChild(cidrInput);
    form.appendChild(reasonSel);
    form.appendChild(expiresInput);
    form.appendChild(submit);

    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      submit.disabled = true;
      try {
        const resp = await postBan(IP_BANS, {
          cidr: cidrInput.value.trim(),
          reason: reasonSel.value,
          expires_at: expiresInput.value
            ? new Date(expiresInput.value).toISOString() : "",
        });
        if (!resp.ok) {
          const body = await resp.text().catch(() => "");
          announce(`IP ban failed: ${resp.status} ${body.slice(0, 80)}`);
          return;
        }
        announce(`${cidrInput.value.trim()} banned.`);
        cidrInput.value = ""; expiresInput.value = "";
        if (onSuccess) onSuccess();
      } catch (err) {
        announce(`IP ban failed: ${String(err).slice(0, 120)}`);
      } finally {
        submit.disabled = false;
      }
    });
    return form;
  }

  function renderUserBansTable(list, onRemoveRefresh) {
    const table = el("table", {
      className: "ms-bans-table",
      attrs: { role: "table" },
    });
    table.appendChild(el("caption", { text: `User bans (${list.length})` }));
    const thead = el("thead");
    const headerRow = el("tr");
    const headers = ["Username", "Reason", "Detail", "Actor", "Banned at", "Expires at", "Actions"];
    for (const h of headers) {
      headerRow.appendChild(el("th", { text: h, attrs: { scope: "col" } }));
    }
    thead.appendChild(headerRow);
    table.appendChild(thead);
    const tbody = el("tbody");
    for (const b of list) {
      const tr = el("tr");
      tr.appendChild(el("td", { text: b.username || "" }));
      tr.appendChild(el("td", { text: b.reason || "" }));
      tr.appendChild(el("td", { text: b.reason_detail || "" }));
      tr.appendChild(el("td", { text: b.actor || "" }));
      tr.appendChild(el("td", { text: b.banned_at || "" }));
      tr.appendChild(el("td", { text: b.expires_at || "indefinite" }));
      const actionTd = el("td");
      const rmBtn = el("button", {
        text: "Unban",
        className: "ms-secondary-btn",
        attrs: {
          type: "button",
          "aria-label": `Remove ban on ${b.username}`,
        },
      });
      rmBtn.addEventListener("click", async () => {
        rmBtn.disabled = true;
        try {
          const url = `${USER_BANS}/${encodeURIComponent(b.username)}/remove`;
          const resp = await removeBan(url);
          if (!resp.ok) {
            announce(`Unban failed: ${resp.status}`);
            rmBtn.disabled = false;
            return;
          }
          announce(`${b.username} unbanned.`);
          if (onRemoveRefresh) onRemoveRefresh();
        } catch (err) {
          announce(`Unban failed: ${String(err).slice(0, 120)}`);
          rmBtn.disabled = false;
        }
      });
      actionTd.appendChild(rmBtn);
      tr.appendChild(actionTd);
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    return table;
  }

  function renderIpBansTable(list, onRemoveRefresh) {
    const table = el("table", {
      className: "ms-bans-table",
      attrs: { role: "table" },
    });
    table.appendChild(el("caption", { text: `IP bans (${list.length})` }));
    const thead = el("thead");
    const headerRow = el("tr");
    const headers = ["CIDR", "Reason", "Actor", "Banned at", "Expires at", "Actions"];
    for (const h of headers) {
      headerRow.appendChild(el("th", { text: h, attrs: { scope: "col" } }));
    }
    thead.appendChild(headerRow);
    table.appendChild(thead);
    const tbody = el("tbody");
    for (const b of list) {
      const tr = el("tr");
      tr.appendChild(el("td", { text: b.cidr || "" }));
      tr.appendChild(el("td", { text: b.reason || "" }));
      tr.appendChild(el("td", { text: b.actor || "" }));
      tr.appendChild(el("td", { text: b.banned_at || "" }));
      tr.appendChild(el("td", { text: b.expires_at || "indefinite" }));
      const actionTd = el("td");
      const rmBtn = el("button", {
        text: "Unban",
        className: "ms-secondary-btn",
        attrs: {
          type: "button",
          "aria-label": `Remove ban on ${b.cidr}`,
        },
      });
      rmBtn.addEventListener("click", async () => {
        rmBtn.disabled = true;
        try {
          const url = `${IP_BANS}/${encodeURIComponent(b.cidr)}/remove`;
          const resp = await removeBan(url);
          if (!resp.ok) {
            announce(`Unban failed: ${resp.status}`);
            rmBtn.disabled = false;
            return;
          }
          announce(`${b.cidr} unbanned.`);
          if (onRemoveRefresh) onRemoveRefresh();
        } catch (err) {
          announce(`Unban failed: ${String(err).slice(0, 120)}`);
          rmBtn.disabled = false;
        }
      });
      actionTd.appendChild(rmBtn);
      tr.appendChild(actionTd);
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    return table;
  }

  async function load(root) {
    if (_abortController) _abortController.abort();
    _abortController = new AbortController();
    clearChildren(root);

    const live = el("div", {
      attrs: {
        id: "ms-bans-live-region",
        "aria-live": "polite",
        class: "ms-sr-only",
      },
    });
    root.appendChild(live);

    root.appendChild(el("h3", { text: "User bans" }));
    root.appendChild(buildUserBanForm(() => load(root)));
    const userBansContainer = el("div", {
      attrs: { id: "ms-user-bans-container" },
    });
    userBansContainer.appendChild(stateMsg("Loading..."));
    root.appendChild(userBansContainer);

    root.appendChild(el("h3", { text: "IP bans" }));
    root.appendChild(buildIpBanForm(() => load(root)));
    const ipBansContainer = el("div", {
      attrs: { id: "ms-ip-bans-container" },
    });
    ipBansContainer.appendChild(stateMsg("Loading..."));
    root.appendChild(ipBansContainer);

    const sig = _abortController.signal;
    const jobs = await Promise.allSettled([
      apiFetch(USER_BANS, { signal: sig }).then(r => r.ok ? r.json() : Promise.reject(r.status)),
      apiFetch(IP_BANS, { signal: sig }).then(r => r.ok ? r.json() : Promise.reject(r.status)),
    ]);
    if (jobs[0].status === "fulfilled") {
      clearChildren(userBansContainer);
      const bans = jobs[0].value.bans || [];
      if (bans.length === 0) {
        userBansContainer.appendChild(stateMsg("No user bans."));
      } else {
        userBansContainer.appendChild(renderUserBansTable(bans, () => load(root)));
      }
    } else if (jobs[0].reason && jobs[0].reason.name !== "AbortError") {
      clearChildren(userBansContainer);
      userBansContainer.appendChild(stateMsg(`Failed: ${String(jobs[0].reason)}`, "alert"));
    }
    if (jobs[1].status === "fulfilled") {
      clearChildren(ipBansContainer);
      const bans = jobs[1].value.bans || [];
      if (bans.length === 0) {
        ipBansContainer.appendChild(stateMsg("No IP bans."));
      } else {
        ipBansContainer.appendChild(renderIpBansTable(bans, () => load(root)));
      }
    } else if (jobs[1].reason && jobs[1].reason.name !== "AbortError") {
      clearChildren(ipBansContainer);
      ipBansContainer.appendChild(stateMsg(`Failed: ${String(jobs[1].reason)}`, "alert"));
    }
    _abortController = null;
  }

  window.renderBansTab = function (rootEl) {
    const root = rootEl || document.getElementById("tab-bans");
    if (!root) return;
    load(root);
  };
})();
