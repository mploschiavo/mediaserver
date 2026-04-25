/* Media Integrity tab — calm status card for the anti-duplicate
 * subsystem.
 *
 * Three panels:
 *   1. Status — when did the last enforce / reconcile run; what was
 *      resolved; what needs review (the only thing surfaced).
 *   2. Servarr adapters — one row per Radarr/Sonarr/Lidarr/Readarr
 *      with last-run outcome + a "Run now" button (admin).
 *   3. Bazarr — same shape, subtitle-flavoured.
 *
 * Design goal: non-technical users read this page and walk away
 * feeling "nothing to do". The only attention-grabber is the
 * "needs review" chip, which lists the (rare) genuinely ambiguous
 * dupes that the reconciler couldn't pick a winner for.
 *
 * XSS: textContent only, never innerHTML with server data.
 * A11y: ARIA live regions, semantic sections, button types.
 */

(function () {
  "use strict";

  const STATUS_ENDPOINT = "/api/media-integrity/status";
  const PROGRESS_ENDPOINT = "/api/media-integrity/progress";
  const RECONCILE_ENDPOINT = "/api/media-integrity/reconcile";
  const ENFORCE_ENDPOINT = "/api/media-integrity/enforce-config";
  const RESOLVE_ENDPOINT = "/api/media-integrity/resolve-review";

  const SPINNER_FRAMES = ["-", "/", "|", "\\"];

  let _abortController = null;
  let _lastReport = null;
  let _expandedAdapters = new Set();

  function clearChildren(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function el(tag, opts) {
    const node = document.createElement(tag);
    if (!opts) return node;
    if (opts.text !== undefined) node.textContent = String(opts.text);
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

  function formatTs(ts) {
    if (!ts) return "never";
    const d = new Date(ts);
    if (isNaN(d.getTime())) return ts;
    return d.toLocaleString();
  }

  function formatRelative(ts) {
    if (!ts) return "never";
    const d = new Date(ts);
    if (isNaN(d.getTime())) return String(ts);
    const diff = Date.now() - d.getTime();
    if (diff < 0) return d.toLocaleString();
    const secs = Math.round(diff / 1000);
    if (secs < 60) return `${secs} sec ago`;
    const mins = Math.round(secs / 60);
    if (mins < 60) return `${mins} min ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return `${hrs} hr ago`;
    const days = Math.round(hrs / 24);
    return `${days} day${days === 1 ? "" : "s"} ago`;
  }

  function formatBytes(n) {
    if (!n || n < 1024) return `${n || 0} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let i = -1;
    let v = n;
    while (v >= 1024 && i < units.length - 1) {
      v /= 1024;
      i++;
    }
    return `${v.toFixed(1)} ${units[i]}`;
  }

  function section(titleText, id) {
    const s = el("section", {
      className: "ms-security-panel",
      attrs: { "aria-labelledby": `${id}-heading` },
    });
    s.appendChild(
      el("h3", { text: titleText, attrs: { id: `${id}-heading` } })
    );
    const body = el("div", { attrs: { id: `${id}-body` } });
    s.appendChild(body);
    return { section: s, body };
  }

  async function fetchJSON(url, signal) {
    const resp = await fetch(url, { credentials: "same-origin", signal });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`${resp.status}: ${text.slice(0, 200)}`);
    }
    return resp.json();
  }

  async function postAction(url, signal, payload) {
    const resp = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: payload ? JSON.stringify(payload) : "{}",
      signal,
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`${resp.status}: ${text.slice(0, 200)}`);
    }
    return resp.json();
  }

  function buildAdapterRows(status) {
    const rows = [];
    const detail = (status.last_reconcile && status.last_reconcile.detail) || {};
    const lastTs = (status.last_reconcile && status.last_reconcile.ts) || null;
    const servarrResults = ((detail.servarr || {}).results || []);
    const byApp = {};
    for (const r of servarrResults) byApp[r.app] = r;

    for (const app of (status.servarr_adapters || [])) {
      const r = byApp[app];
      rows.push({
        app,
        kind: "servarr",
        hasRun: !!r,
        ts: r ? lastTs : null,
        resolved: r ? (r.resolved || []).length : null,
        freed: r ? (r.bytes_freed || sumBytes(r.resolved)) : null,
        needsReview: r ? (r.needs_review || []) : [],
        failures: r ? (r.failures || []).length : null,
      });
    }
    if (status.bazarr_present) {
      const b = detail.bazarr;
      rows.push({
        app: "bazarr",
        kind: "bazarr",
        hasRun: !!b,
        ts: b ? lastTs : null,
        resolved: b ? (b.resolved || []).length : null,
        freed: b ? (b.total_bytes_freed || 0) : null,
        needsReview: b ? (b.needs_review || []) : [],
        failures: b ? (b.failures || []).length : null,
      });
    }
    return rows;
  }

  function sumBytes(resolved) {
    if (!Array.isArray(resolved)) return 0;
    let total = 0;
    for (const item of resolved) {
      if (item && typeof item.bytes_freed === "number") total += item.bytes_freed;
    }
    return total;
  }

  function renderWarningBanner(host, missing) {
    if (!missing || !missing.length) return;
    const banner = el("div", {
      className: "ms-mi-warning",
      attrs: { role: "status", "aria-live": "polite" },
    });
    banner.appendChild(
      el("strong", { text: "Warning: ", className: "ms-mi-warning-label" })
    );
    banner.appendChild(
      el("span", {
        text: `Missing API keys for: ${missing.join(", ")}`,
      })
    );
    host.appendChild(banner);
  }

  function renderDryRunCallout(host) {
    const callout = el("div", {
      className: "ms-mi-dryrun-callout",
      attrs: { role: "status", "aria-live": "polite" },
    });
    callout.appendChild(
      el("strong", { text: "Dry run — nothing was deleted" })
    );
    host.appendChild(callout);
  }

  function renderReviewCandidates(host, row, onResolve) {
    const list = el("ul", { className: "ms-mi-review-candidates" });
    for (const item of row.needsReview) {
      const li = el("li", { className: "ms-mi-review-item" });
      const titleLine = el("div", { className: "ms-mi-review-title" });
      titleLine.appendChild(
        el("span", { text: item.release_title || `Release ${item.release_id}` })
      );
      if (row.kind === "bazarr") {
        const meta = [];
        if (item.language) meta.push(item.language);
        if (item.forced) meta.push("forced");
        if (item.hi) meta.push("hi");
        if (meta.length) {
          titleLine.appendChild(
            el("span", {
              text: ` (${meta.join(", ")})`,
              className: "ms-mi-review-meta",
            })
          );
        }
      }
      li.appendChild(titleLine);

      const candidates =
        row.kind === "bazarr"
          ? (item.candidate_paths || [])
          : (item.candidate_file_ids || []);
      const candWrap = el("ul", { className: "ms-mi-candidate-list" });
      for (const cand of candidates) {
        const candLi = el("li", { className: "ms-mi-candidate" });
        candLi.appendChild(
          el("span", { text: String(cand), className: "ms-mi-candidate-label" })
        );
        const losers = candidates.filter((c) => c !== cand).map(String);
        const ariaLabel =
          losers.length > 0
            ? `Keep ${cand} and delete ${losers.join(", ")}`
            : `Keep ${cand}`;
        const keepBtn = el("button", {
          text: "Keep this one",
          className: "ms-btn ms-btn-small",
          attrs: { type: "button", "aria-label": ariaLabel },
        });
        keepBtn.addEventListener("click", () => {
          const payload = { app: row.app, release_id: item.release_id };
          if (row.kind === "bazarr") {
            payload.winner_sub_path = String(cand);
            if (item.release_kind) payload.release_kind = item.release_kind;
            if (item.language) payload.language = item.language;
            payload.forced = !!item.forced;
            payload.hi = !!item.hi;
          } else {
            payload.winner_file_id = cand;
          }
          onResolve(payload, keepBtn);
        });
        candLi.appendChild(keepBtn);
        candWrap.appendChild(candLi);
      }
      li.appendChild(candWrap);
      list.appendChild(li);
    }
    host.appendChild(list);
  }

  function renderAdapterTable(body, status, onResolve) {
    const rows = buildAdapterRows(status);
    if (!rows.length) {
      body.appendChild(
        el("p", {
          text: "No adapters configured.",
          className: "ms-mi-empty",
        })
      );
      return;
    }

    const table = el("table", { className: "ms-mi-adapter-table" });
    const thead = el("thead");
    const headRow = el("tr");
    const headers = [
      "Adapter",
      "Resolved",
      "Freed",
      "Needs review",
      "Failures",
      "Last run",
    ];
    for (const h of headers) {
      headRow.appendChild(
        el("th", { text: h, attrs: { scope: "col" } })
      );
    }
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = el("tbody");
    const disclosureRows = [];
    rows.forEach((row, idx) => {
      const tr = el("tr");
      tr.appendChild(
        el("th", { text: row.app, attrs: { scope: "row" } })
      );
      if (!row.hasRun) {
        for (let i = 0; i < 4; i++) {
          tr.appendChild(el("td", { text: "—" }));
        }
        tr.appendChild(el("td", { text: "never" }));
      } else {
        tr.appendChild(el("td", { text: String(row.resolved || 0) }));
        tr.appendChild(el("td", { text: formatBytes(row.freed || 0) }));

        const reviewCell = el("td");
        const reviewCount = row.needsReview.length;
        if (reviewCount > 0) {
          const panelId = `ms-mi-review-panel-${idx}`;
          const expanded = _expandedAdapters.has(row.app);
          const btn = el("button", {
            text: String(reviewCount),
            className: "ms-btn ms-btn-link",
            attrs: {
              type: "button",
              "aria-expanded": expanded ? "true" : "false",
              "aria-controls": panelId,
              "aria-label": `${reviewCount} releases need review for ${row.app}; toggle details`,
            },
          });
          btn.addEventListener("click", () => {
            if (_expandedAdapters.has(row.app)) {
              _expandedAdapters.delete(row.app);
            } else {
              _expandedAdapters.add(row.app);
            }
            const nowExpanded = _expandedAdapters.has(row.app);
            btn.setAttribute("aria-expanded", nowExpanded ? "true" : "false");
            disclosure.hidden = !nowExpanded;
          });
          reviewCell.appendChild(btn);

          const disclosure = el("tr", {
            className: "ms-mi-disclosure-row",
          });
          disclosure.hidden = !expanded;
          const disclosureCell = el("td", {
            attrs: { colspan: String(headers.length), id: panelId },
          });
          renderReviewCandidates(disclosureCell, row, onResolve);
          disclosure.appendChild(disclosureCell);
          disclosureRows.push({ after: tr, node: disclosure });
        } else {
          reviewCell.textContent = "0";
        }
        tr.appendChild(reviewCell);

        tr.appendChild(el("td", { text: String(row.failures || 0) }));
        tr.appendChild(el("td", { text: formatRelative(row.ts) }));
      }
      tbody.appendChild(tr);
      const pending = disclosureRows.find((d) => d.after === tr);
      if (pending) tbody.appendChild(pending.node);
    });
    table.appendChild(tbody);
    body.appendChild(table);
  }

  function renderStatusPanel(body, status, onResolve) {
    clearChildren(body);
    const lastEnforce = status.last_enforce || {};
    const lastReconcile = status.last_reconcile || {};

    const list = el("dl", { className: "ms-mi-status-grid" });
    list.appendChild(el("dt", { text: "Policy version" }));
    list.appendChild(
      el("dd", { text: String(status.policy_version || "?") })
    );

    list.appendChild(el("dt", { text: "Last config enforce" }));
    list.appendChild(el("dd", { text: formatTs(lastEnforce.ts) }));

    list.appendChild(el("dt", { text: "Last reconcile" }));
    list.appendChild(el("dd", { text: formatTs(lastReconcile.ts) }));

    list.appendChild(el("dt", { text: "Bazarr configured" }));
    list.appendChild(
      el("dd", { text: status.bazarr_present ? "yes" : "no" })
    );
    body.appendChild(list);

    renderAdapterTable(body, status, onResolve);

    const detail = lastReconcile.detail || {};
    const servarr = detail.servarr || {};
    const bazarr = detail.bazarr || {};
    const summary = el("p", { className: "ms-mi-summary" });
    const resolved =
      (servarr.total_resolved || 0) +
      ((bazarr && bazarr.resolved) ? bazarr.resolved.length : 0);
    const freed =
      (servarr.total_bytes_freed || 0) +
      ((bazarr && bazarr.total_bytes_freed) || 0);
    summary.textContent =
      `Resolved ${resolved} duplicate${resolved === 1 ? "" : "s"}; ` +
      `freed ${formatBytes(freed)} in the last pass.`;
    body.appendChild(summary);
  }

  function startSpinner(span) {
    let i = 0;
    span.textContent = SPINNER_FRAMES[0];
    const id = setInterval(() => {
      i = (i + 1) % SPINNER_FRAMES.length;
      span.textContent = SPINNER_FRAMES[i];
    }, 150);
    return id;
  }

  async function pollProgress(signal) {
    while (true) {
      let data;
      try {
        data = await fetchJSON(PROGRESS_ENDPOINT, signal);
      } catch (exc) {
        return;
      }
      if (!data || data.in_progress === false) return;
      await new Promise((r) => setTimeout(r, 500));
      if (signal && signal.aborted) return;
    }
  }

  function makeRunController(buttons, clickedBtn, originalLabel) {
    const spinnerSpan = el("span", {
      className: "ms-mi-spinner",
      attrs: { "aria-live": "polite", "aria-hidden": "false" },
    });
    const elapsedSpan = el("span", { className: "ms-mi-elapsed" });
    const labelSpan = el("span", { text: "Running... ", className: "ms-mi-running-label" });

    const originalChildren = Array.from(clickedBtn.childNodes);
    clearChildren(clickedBtn);
    clickedBtn.appendChild(labelSpan);
    clickedBtn.appendChild(spinnerSpan);
    clickedBtn.appendChild(elapsedSpan);

    for (const b of buttons) {
      b.disabled = true;
      b.setAttribute("aria-disabled", "true");
    }

    const spinId = startSpinner(spinnerSpan);
    const startedAt = Date.now();
    let elapsedId = null;
    const elapsedDelay = setTimeout(() => {
      elapsedId = setInterval(() => {
        const secs = Math.round((Date.now() - startedAt) / 1000);
        elapsedSpan.textContent = ` (${secs}s)`;
      }, 1000);
    }, 2000);

    return {
      stop() {
        clearInterval(spinId);
        clearTimeout(elapsedDelay);
        if (elapsedId) clearInterval(elapsedId);
        for (const b of buttons) {
          b.disabled = false;
          b.removeAttribute("aria-disabled");
        }
        clearChildren(clickedBtn);
        for (const c of originalChildren) clickedBtn.appendChild(c);
        if (!originalChildren.length) clickedBtn.textContent = originalLabel;
      },
    };
  }

  function renderActions(body, handlers) {
    const wrap = el("div", { className: "ms-mi-actions" });
    const reconcileBtn = el("button", {
      text: "Reconcile now",
      className: "ms-btn",
      attrs: {
        type: "button",
        "aria-label": "Reconcile duplicates now",
      },
    });
    const enforceBtn = el("button", {
      text: "Enforce config now",
      className: "ms-btn",
      attrs: {
        type: "button",
        "aria-label": "Enforce policy on every adapter now",
      },
    });

    const dryRunWrap = el("label", { className: "ms-mi-dryrun-toggle" });
    const dryRunCb = el("input", {
      attrs: { type: "checkbox", "aria-label": "Dry run reconcile" },
    });
    dryRunWrap.appendChild(dryRunCb);
    dryRunWrap.appendChild(el("span", { text: " Dry run" }));

    reconcileBtn.addEventListener("click", () =>
      handlers.onReconcile(reconcileBtn, [reconcileBtn, enforceBtn], dryRunCb.checked)
    );
    enforceBtn.addEventListener("click", () =>
      handlers.onEnforce(enforceBtn, [reconcileBtn, enforceBtn])
    );

    wrap.appendChild(reconcileBtn);
    wrap.appendChild(dryRunWrap);
    wrap.appendChild(enforceBtn);
    body.appendChild(wrap);
  }

  async function load(root) {
    if (_abortController) _abortController.abort();
    _abortController = new AbortController();
    clearChildren(root);

    const header = el("header", { className: "ms-tab-header" });
    header.appendChild(el("h2", { text: "Media integrity" }));
    header.appendChild(
      el("p", {
        text:
          "Silently heals duplicate movies, episodes, tracks, books, and " +
          "subtitles across every *arr in the stack.",
        className: "ms-tab-subtitle",
      })
    );
    const warningHost = el("div", { className: "ms-mi-warning-host" });
    header.appendChild(warningHost);
    root.appendChild(header);

    const actions = el("div", { attrs: { id: "ms-mi-actions-body" } });
    root.appendChild(actions);

    const status = section("Status", "ms-mi-status");
    const dryRunHost = el("div", { className: "ms-mi-dryrun-host" });
    const actionResult = el("div", {
      className: "ms-mi-action-result",
      attrs: { role: "status", "aria-live": "polite" },
    });
    root.appendChild(status.section);
    root.appendChild(dryRunHost);
    root.appendChild(actionResult);

    status.body.appendChild(stateMsg("Loading..."));

    async function resolveReview(payload, btn) {
      const prevText = btn.textContent;
      btn.disabled = true;
      btn.setAttribute("aria-disabled", "true");
      btn.textContent = "Working...";
      try {
        await postAction(RESOLVE_ENDPOINT, _abortController.signal, payload);
        actionResult.textContent =
          `Resolved review for ${payload.app} release ${payload.release_id}.`;
        await refresh();
      } catch (exc) {
        btn.disabled = false;
        btn.removeAttribute("aria-disabled");
        btn.textContent = prevText;
        actionResult.textContent =
          `Resolve failed: ${String(exc).slice(0, 200)}`;
      }
    }

    async function refresh() {
      try {
        const data = await fetchJSON(STATUS_ENDPOINT, _abortController.signal);
        clearChildren(warningHost);
        if (Array.isArray(data.missing_api_keys)) {
          renderWarningBanner(warningHost, data.missing_api_keys);
        }
        renderStatusPanel(status.body, data, resolveReview);
      } catch (exc) {
        if (exc && exc.name === "AbortError") return;
        clearChildren(status.body);
        status.body.appendChild(
          stateMsg(`Error: ${String(exc).slice(0, 200)}`, "alert")
        );
      }
    }

    async function runReconcile(clickedBtn, allBtns, dryRun) {
      actionResult.textContent = "";
      clearChildren(dryRunHost);
      const ctrl = makeRunController(allBtns, clickedBtn, "Reconcile now");
      const url = dryRun ? `${RECONCILE_ENDPOINT}?dry_run=1` : RECONCILE_ENDPOINT;
      const progressAbort = new AbortController();
      const progressPromise = pollProgress(progressAbort.signal);
      try {
        const report = await postAction(url, _abortController.signal);
        _lastReport = report;
        progressAbort.abort();
        await progressPromise;
        ctrl.stop();
        if (report && report.dry_run) {
          renderDryRunCallout(dryRunHost);
          actionResult.textContent = "Dry run complete.";
        } else {
          actionResult.textContent = "Reconcile complete.";
        }
        await refresh();
      } catch (exc) {
        progressAbort.abort();
        ctrl.stop();
        actionResult.textContent =
          `Reconcile failed: ${String(exc).slice(0, 200)}`;
      }
    }

    async function runEnforce(clickedBtn, allBtns) {
      actionResult.textContent = "";
      clearChildren(dryRunHost);
      const ctrl = makeRunController(allBtns, clickedBtn, "Enforce config now");
      const progressAbort = new AbortController();
      const progressPromise = pollProgress(progressAbort.signal);
      try {
        await postAction(ENFORCE_ENDPOINT, _abortController.signal);
        progressAbort.abort();
        await progressPromise;
        ctrl.stop();
        actionResult.textContent = "Enforce complete.";
        await refresh();
      } catch (exc) {
        progressAbort.abort();
        ctrl.stop();
        actionResult.textContent =
          `Enforce failed: ${String(exc).slice(0, 200)}`;
      }
    }

    renderActions(actions, {
      onReconcile: runReconcile,
      onEnforce: runEnforce,
    });
    await refresh();
    _abortController = null;
  }

  window.renderMediaIntegrityTab = function (rootEl) {
    const root =
      rootEl || document.getElementById("tab-media-integrity");
    if (!root) return;
    load(root);
  };
})();
