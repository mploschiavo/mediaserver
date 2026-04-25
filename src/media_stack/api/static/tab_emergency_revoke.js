/* Emergency revoke tab — last-resort admin action.
 *
 * One-click-plus-strong-confirmation to:
 *   1. Revoke every live session across every provider.
 *   2. Rotate the controller's bearer-token signing secret.
 *   3. Force a password rotation on next sign-in for admin users.
 *   4. Audit the event with the operator's identity + timestamp.
 *
 * Design goals:
 *   - Impossible to trigger accidentally — two-step confirmation.
 *   - Operator types a confirmation phrase before the button unlocks.
 *   - Idempotency-Key prevents a stuck click from firing twice.
 *   - Outcome report shown inline (which providers succeeded/failed).
 *   - A11y: focus management on dialog, aria-live on outcome.
 *
 * XSS-safe + first-class quality.
 */

(function () {
  "use strict";

  const EMERGENCY_REVOKE = "/api/emergency-revoke-all";
  const CONFIRM_PHRASE = "REVOKE EVERYTHING";

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

  function idempotencyKey() {
    const b = new Uint8Array(16);
    crypto.getRandomValues(b);
    return Array.from(b, x => x.toString(16).padStart(2, "0")).join("");
  }

  function renderOutcome(root, payload) {
    const existing = document.getElementById("ms-emergency-outcome");
    if (existing) existing.remove();
    const out = el("div", {
      className: "ms-emergency-outcome",
      attrs: {
        id: "ms-emergency-outcome",
        role: "status",
        "aria-live": "polite",
      },
    });
    out.appendChild(el("h4", {
      text: payload.ok
        ? "Emergency revoke complete."
        : "Emergency revoke completed with errors.",
    }));
    const providers = payload.provider_results || {};
    if (Object.keys(providers).length) {
      const list = el("ul", { className: "ms-emergency-results" });
      for (const [name, status] of Object.entries(providers)) {
        const li = el("li");
        const badge = el("span", {
          text: status === "ok" ? "✓" : "⚠",
          className: status === "ok"
            ? "ms-severity-ok" : "ms-severity-warn",
        });
        li.appendChild(badge);
        li.appendChild(el("span", { text: ` ${name}: ${status}` }));
        list.appendChild(li);
      }
      out.appendChild(list);
    }
    if (payload.secrets_rotated) {
      out.appendChild(el("p", {
        text: "Controller bearer-token signing secret rotated.",
      }));
    }
    if (payload.forced_rotations) {
      out.appendChild(el("p", {
        text: `${payload.forced_rotations} admin user(s) flagged for password rotation on next login.`,
      }));
    }
    root.appendChild(out);
  }

  function renderConfirmationDialog(root, onConfirmed) {
    clearChildren(root);
    const dialog = el("div", {
      className: "ms-emergency-dialog",
      attrs: {
        role: "dialog",
        "aria-modal": "true",
        "aria-labelledby": "ms-emergency-heading",
        "aria-describedby": "ms-emergency-desc",
      },
    });

    dialog.appendChild(el("h2", {
      text: "⚠ Emergency revoke",
      attrs: { id: "ms-emergency-heading" },
    }));

    const desc = el("div", { attrs: { id: "ms-emergency-desc" } });
    desc.appendChild(el("p", {
      text: "This action cannot be undone in one click. It will:",
    }));
    const list = el("ul");
    list.appendChild(el("li", {
      text: "Revoke every live session on every provider "
        + "(controller, Authelia, Jellyfin, ...).",
    }));
    list.appendChild(el("li", {
      text: "Rotate the controller's bearer-token signing secret.",
    }));
    list.appendChild(el("li", {
      text: "Flag every admin-role user for forced password "
        + "rotation on next sign-in.",
    }));
    list.appendChild(el("li", {
      text: "Write an emergency_revoke_all entry to the audit log "
        + "with your identity and the current timestamp.",
    }));
    desc.appendChild(list);
    desc.appendChild(el("p", {
      text: "Every admin and end user in this deployment will need "
        + "to sign in again. Use only during an active incident.",
    }));
    dialog.appendChild(desc);

    const confirmLabel = el("label", {
      text: `Type "${CONFIRM_PHRASE}" to unlock the button:`,
      attrs: { for: "ms-emergency-confirm" },
    });
    const confirmInput = el("input", {
      attrs: {
        id: "ms-emergency-confirm",
        type: "text",
        autocomplete: "off",
        "aria-describedby": "ms-emergency-desc",
      },
    });
    dialog.appendChild(confirmLabel);
    dialog.appendChild(confirmInput);

    const reasonLabel = el("label", {
      text: "Incident reason (required, goes to the audit log):",
      attrs: { for: "ms-emergency-reason" },
    });
    const reasonInput = el("input", {
      attrs: {
        id: "ms-emergency-reason",
        type: "text",
        required: "required",
        placeholder: "Active credential leak via ...",
      },
    });
    dialog.appendChild(reasonLabel);
    dialog.appendChild(reasonInput);

    const btnRow = el("div", { className: "ms-emergency-actions" });
    const cancelBtn = el("button", {
      text: "Cancel",
      className: "ms-secondary-btn",
      attrs: { type: "button" },
    });
    const confirmBtn = el("button", {
      text: "Revoke everything now",
      className: "ms-danger-btn",
      attrs: {
        type: "button",
        disabled: "disabled",
        "aria-describedby": "ms-emergency-desc",
      },
    });
    btnRow.appendChild(cancelBtn);
    btnRow.appendChild(confirmBtn);
    dialog.appendChild(btnRow);

    cancelBtn.addEventListener("click", () => {
      clearChildren(root);
      renderInitial(root);
    });

    confirmInput.addEventListener("input", () => {
      confirmBtn.disabled = confirmInput.value !== CONFIRM_PHRASE
        || reasonInput.value.trim().length < 5;
    });
    reasonInput.addEventListener("input", () => {
      confirmBtn.disabled = confirmInput.value !== CONFIRM_PHRASE
        || reasonInput.value.trim().length < 5;
    });

    confirmBtn.addEventListener("click", async () => {
      confirmBtn.disabled = true;
      cancelBtn.disabled = true;
      confirmBtn.setAttribute("aria-busy", "true");
      try {
        const resp = await apiFetch(EMERGENCY_REVOKE, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": idempotencyKey(),
          },
          body: JSON.stringify({
            reason: reasonInput.value.trim(),
          }),
        });
        if (!resp.ok) {
          const body = await resp.text().catch(() => "");
          renderOutcome(root, {
            ok: false, error: `HTTP ${resp.status}: ${body.slice(0, 200)}`,
          });
          return;
        }
        const payload = await resp.json();
        renderOutcome(root, payload);
      } catch (err) {
        renderOutcome(root, { ok: false, error: String(err).slice(0, 200) });
      } finally {
        confirmBtn.setAttribute("aria-busy", "false");
      }
    });

    root.appendChild(dialog);
    confirmInput.focus();
  }

  function renderInitial(root) {
    clearChildren(root);
    const intro = el("section", {
      className: "ms-emergency-intro",
    });
    intro.appendChild(el("h3", { text: "Emergency actions" }));
    intro.appendChild(el("p", {
      text: "Use this panel only when an active incident requires "
        + "immediate lockout of every session in the deployment.",
    }));
    const startBtn = el("button", {
      text: "Start emergency revoke...",
      className: "ms-danger-btn",
      attrs: {
        type: "button",
        "aria-haspopup": "dialog",
      },
    });
    startBtn.addEventListener("click", () => {
      renderConfirmationDialog(root, () => {});
    });
    intro.appendChild(startBtn);
    root.appendChild(intro);
  }

  window.renderEmergencyRevokeTab = function (rootEl) {
    const root = rootEl || document.getElementById("tab-emergency-revoke");
    if (!root) return;
    renderInitial(root);
  };
})();
