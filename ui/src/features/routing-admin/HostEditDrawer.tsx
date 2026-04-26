import { useState, useEffect } from "react";
import { Drawer as VaulDrawer } from "vaul";
import { X, AlertTriangle, Save, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  useRoutingV2Mutation,
  type RoutingV2HostEntry,
  type RoutingV2Config,
} from "./hooks";

interface HostEditDrawerProps {
  open: boolean;
  hostIndex: number | null;
  config: RoutingV2Config | null;
  onClose: () => void;
}

/**
 * Edit drawer for one ``HostEntry``. Lets the operator change auth
 * gate, path prefix, websocket, maintenance flag, TLS cert binding,
 * and add/remove aliases. Saves via POST /api/routing/v2 (deep-merge
 * partial update); errors surface field-level next to the input.
 *
 * "Delete this host" lives at the bottom for the same row — a
 * destructive action, gated behind a second click.
 */
export function HostEditDrawer({
  open,
  hostIndex,
  config,
  onClose,
}: HostEditDrawerProps) {
  const mutation = useRoutingV2Mutation();
  const original =
    hostIndex !== null && config ? config.hosts[hostIndex] : null;

  const [draft, setDraft] = useState<RoutingV2HostEntry | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [aliasInput, setAliasInput] = useState("");

  // Reset the draft whenever the row changes.
  useEffect(() => {
    setDraft(original ? structuredClone(original) : null);
    setConfirmDelete(false);
    setAliasInput("");
  }, [original]);

  if (!open || !draft || !config || hostIndex === null) {
    return null;
  }

  const update = (patch: Partial<RoutingV2HostEntry>) =>
    setDraft((d) => (d ? { ...d, ...patch } : d));

  const handleSave = () => {
    if (!draft) return;
    const newHosts = [...config.hosts];
    newHosts[hostIndex] = draft;
    mutation.mutate(
      { hosts: newHosts },
      { onSuccess: () => onClose() },
    );
  };

  const handleDelete = () => {
    const newHosts = config.hosts.filter((_, i) => i !== hostIndex);
    mutation.mutate(
      { hosts: newHosts },
      { onSuccess: () => onClose() },
    );
  };

  const handleAddAlias = () => {
    const a = aliasInput.trim();
    if (!a) return;
    update({ aliases: [...(draft.aliases ?? []), a] });
    setAliasInput("");
  };

  const handleRemoveAlias = (idx: number) => {
    update({
      aliases: (draft.aliases ?? []).filter((_, i) => i !== idx),
    });
  };

  return (
    <VaulDrawer.Root
      direction="right"
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <VaulDrawer.Portal>
        <VaulDrawer.Overlay className="fixed inset-0 z-50 bg-[color-mix(in_oklab,var(--color-bg)_70%,transparent)] backdrop-blur-sm" />
        <VaulDrawer.Content
          className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col border-l border-border bg-bg-1 outline-none"
          data-testid="host-edit-drawer"
        >
          <header className="flex items-start justify-between gap-3 border-b border-border p-4">
            <div className="flex flex-col gap-1">
              <VaulDrawer.Title className="text-base font-semibold leading-none tracking-tight">
                Edit hostname
              </VaulDrawer.Title>
              <VaulDrawer.Description className="text-xs text-fg-muted">
                {draft.canonical} · role {draft.role || "—"}
              </VaulDrawer.Description>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="rounded-sm p-1 text-fg-muted [@media(hover:hover)]:hover:text-fg"
              aria-label="Close drawer"
              data-testid="host-edit-drawer-close"
            >
              <X className="size-4" aria-hidden />
            </button>
          </header>

          <div className="flex-1 overflow-y-auto p-4">
            <form
              className="flex flex-col gap-4"
              onSubmit={(e) => {
                e.preventDefault();
                handleSave();
              }}
            >
              <Field label="Canonical hostname">
                <input
                  type="text"
                  value={draft.canonical}
                  onChange={(e) => update({ canonical: e.target.value })}
                  className="w-full rounded-md border border-border bg-bg-1 px-2 py-1 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-ring"
                  data-testid="host-edit-canonical"
                />
              </Field>

              <Field
                label="Service"
                hint="Internal service id this hostname forwards to (e.g. jellyfin, sonarr)."
              >
                <input
                  type="text"
                  value={draft.service_id}
                  onChange={(e) => update({ service_id: e.target.value })}
                  className="w-full rounded-md border border-border bg-bg-1 px-2 py-1 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-ring"
                  data-testid="host-edit-service-id"
                />
              </Field>

              <Field
                label="Path prefix override"
                hint='Empty = forward all paths ("/"). Set to e.g. "/apps" if this host should only own a subpath.'
              >
                <input
                  type="text"
                  value={draft.path_prefix ?? ""}
                  onChange={(e) => update({ path_prefix: e.target.value })}
                  placeholder="/"
                  className="w-full rounded-md border border-border bg-bg-1 px-2 py-1 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-ring"
                  data-testid="host-edit-path-prefix"
                />
              </Field>

              <Field
                label="Auth gate"
                hint="required = traffic must be authenticated by Authelia. none = open. Authelia itself must be 'none' or operators get locked out."
              >
                <select
                  value={draft.auth?.gate ?? "none"}
                  onChange={(e) =>
                    update({
                      auth: {
                        gate: e.target.value as "required" | "optional" | "none",
                        provider: draft.auth?.provider ?? "authelia",
                      },
                    })
                  }
                  className="w-full rounded-md border border-border bg-bg-1 px-2 py-1 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-ring"
                  data-testid="host-edit-auth-gate"
                >
                  <option value="required">required</option>
                  <option value="optional">optional</option>
                  <option value="none">none</option>
                </select>
              </Field>

              <Field
                label="TLS cert id"
                hint="References certs[]. Empty = no TLS preference (uses defaults)."
              >
                <input
                  type="text"
                  value={draft.tls?.cert_id ?? ""}
                  onChange={(e) =>
                    update({
                      tls: {
                        cert_id: e.target.value,
                        force_https: draft.tls?.force_https ?? true,
                      },
                    })
                  }
                  placeholder="(none)"
                  className="w-full rounded-md border border-border bg-bg-1 px-2 py-1 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-ring"
                  data-testid="host-edit-cert-id"
                />
              </Field>

              <ToggleRow
                label="WebSocket"
                hint="Allow HTTP→WebSocket upgrades on this host."
                value={draft.websocket ?? false}
                onChange={(v) => update({ websocket: v })}
                testid="host-edit-websocket"
              />

              <ToggleRow
                label="Maintenance mode"
                hint="If on, this host returns 503 to every request. Use during reindex / migrations."
                value={draft.maintenance ?? false}
                onChange={(v) => update({ maintenance: v })}
                testid="host-edit-maintenance"
              />

              <Field
                label="Aliases"
                hint="Other hostnames that 301-redirect to the canonical."
              >
                <div className="flex flex-col gap-1.5">
                  {(draft.aliases ?? []).map((a, idx) => (
                    <div
                      key={`${a}-${idx}`}
                      className="flex items-center gap-2"
                    >
                      <code className="flex-1 truncate rounded bg-bg-2 px-2 py-1 text-xs text-fg">
                        {a}
                      </code>
                      <button
                        type="button"
                        onClick={() => handleRemoveAlias(idx)}
                        className="rounded p-1 text-danger hover:bg-danger/10"
                        aria-label={`Remove alias ${a}`}
                        data-testid={`host-edit-alias-remove-${idx}`}
                      >
                        <Trash2 className="size-3.5" aria-hidden />
                      </button>
                    </div>
                  ))}
                  <div className="flex gap-2">
                    <input
                      type="text"
                      value={aliasInput}
                      onChange={(e) => setAliasInput(e.target.value)}
                      placeholder="alt.example.com"
                      className="flex-1 rounded-md border border-border bg-bg-1 px-2 py-1 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-ring"
                      data-testid="host-edit-alias-input"
                    />
                    <Button
                      type="button"
                      variant="outline"
                      onClick={handleAddAlias}
                      disabled={!aliasInput.trim()}
                      data-testid="host-edit-alias-add"
                    >
                      Add
                    </Button>
                  </div>
                </div>
              </Field>

              {mutation.error ? (
                <div
                  role="alert"
                  className="flex items-start gap-2 rounded-md border border-danger/40 bg-danger/10 p-2 text-xs text-danger"
                  data-testid="host-edit-error"
                >
                  <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
                  <span>{(mutation.error as Error).message}</span>
                </div>
              ) : null}
            </form>
          </div>

          <footer className="flex items-center gap-2 border-t border-border p-4">
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                if (!confirmDelete) {
                  setConfirmDelete(true);
                  return;
                }
                handleDelete();
              }}
              disabled={mutation.isPending}
              data-testid="host-edit-delete"
              className={
                confirmDelete
                  ? "border-danger text-danger hover:bg-danger/10"
                  : "text-danger hover:bg-danger/10"
              }
            >
              <Trash2 className="size-3.5" />
              {confirmDelete ? "Confirm delete" : "Delete host"}
            </Button>
            <div className="flex-1" />
            <Button type="button" variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button
              type="button"
              onClick={handleSave}
              disabled={mutation.isPending}
              data-testid="host-edit-save"
            >
              <Save className="size-3.5" />
              {mutation.isPending ? "Saving…" : "Save"}
            </Button>
          </footer>
        </VaulDrawer.Content>
      </VaulDrawer.Portal>
    </VaulDrawer.Root>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs font-medium uppercase tracking-wide text-fg-faint">
        {label}
      </span>
      {children}
      {hint ? (
        <span className="text-[11px] text-fg-muted">{hint}</span>
      ) : null}
    </label>
  );
}

function ToggleRow({
  label,
  hint,
  value,
  onChange,
  testid,
}: {
  label: string;
  hint?: string;
  value: boolean;
  onChange: (v: boolean) => void;
  testid?: string;
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="flex flex-col gap-0.5">
        <span className="text-xs font-medium uppercase tracking-wide text-fg-faint">
          {label}
        </span>
        {hint ? (
          <span className="text-[11px] text-fg-muted">{hint}</span>
        ) : null}
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={value}
        onClick={() => onChange(!value)}
        data-testid={testid}
        className={
          value
            ? "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full bg-success transition-colors"
            : "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full bg-bg-3 transition-colors"
        }
      >
        <span
          className={
            value
              ? "inline-block size-3.5 translate-x-5 rounded-full bg-white shadow transition-transform"
              : "inline-block size-3.5 translate-x-1 rounded-full bg-white shadow transition-transform"
          }
        />
      </button>
    </div>
  );
}
