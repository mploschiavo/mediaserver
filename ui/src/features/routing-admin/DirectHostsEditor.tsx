// Per-role hostname override editor.
//
// `direct_hosts` is a map of role → hostname (e.g.
// `{"media_server": "jf.iomio.io", "auth": "auth.iomio.io"}`). The
// backend resolves the role to a service id (`media_server` →
// jellyfin/plex/emby via technology_bindings, `auth` → the configured
// auth provider, anything else as a literal service id) and writes
// matching Envoy vhosts + K8s Ingress rules.
//
// The Routing editor surfaces this as a mapping table: each row is
// one role/hostname pair. Roles can be picked from a curated list
// (the well-known roles plus the in-stack service ids) or typed as
// free text. Hostnames are validated client-side for format,
// duplicates against each other, and conflict with the gateway_host
// the operator just set.
//
// Internal state is an *array* of rows (not a map) so the editor can
// keep an empty-hostname row visible while the operator types — a
// pure-map model would drop the row mid-edit. The committed shape
// flows out through `onChange` as a Record<string, string>, which is
// what the routing API takes.

import { useId, useMemo } from "react";
import { Plus, Trash2, AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

// Well-known roles the controller resolves specially. Everything else
// is taken as a literal service id (e.g. `sonarr`, `radarr`).
const WELL_KNOWN_ROLES = [
  {
    value: "media_server",
    label: "Media server",
    hint: "Jellyfin / Plex / Emby (whichever your stack uses)",
  },
  {
    value: "auth",
    label: "Auth provider",
    hint: "Authelia portal hostname",
  },
] as const;

// Service ids the operator can also use as direct_hosts roles. The
// controller treats unknown roles as literal service ids, so any
// app that's running in the stack can have its own direct hostname.
const SERVICE_ROLES = [
  "sonarr",
  "radarr",
  "lidarr",
  "readarr",
  "bazarr",
  "prowlarr",
  "qbittorrent",
  "sabnzbd",
  "jellyseerr",
  "homepage",
  "tautulli",
  "maintainerr",
] as const;

const CUSTOM_ROLE = "__custom__";

export interface DirectHostsEditorProps {
  /** Current map of role → hostname. Order is not significant. */
  value: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
  /**
   * Gateway host the operator just set, used for the "this hostname
   * is the same as your gateway" warning. Optional — when missing,
   * the warning is suppressed.
   */
  gatewayHost?: string;
}

interface Row {
  role: string;
  host: string;
  /** Stable key for React; the role can change mid-edit. */
  k: string;
}

interface RowError {
  role?: string;
  host?: string;
  hostWarning?: string;
}

function looksLikeHost(s: string): boolean {
  if (!s) return false;
  if (/[\s/]/.test(s)) return false;
  if (s.includes("://")) return false;
  // FQDN-ish: must contain a dot somewhere. A bare label like
  // `jellyfin` won't reach the operator's browser via DNS, so warn
  // unless explicitly an IP, which we accept (rare but legal).
  if (!s.includes(".")) return false;
  // Letters, digits, hyphens, dots only.
  if (!/^[a-z0-9.-]+$/i.test(s)) return false;
  // No leading/trailing dots/hyphens.
  if (/^[-.]/.test(s) || /[-.]$/.test(s)) return false;
  return true;
}

function isWellKnownRole(role: string): boolean {
  return WELL_KNOWN_ROLES.some((r) => r.value === role);
}

function describeRole(role: string): string | null {
  const wk = WELL_KNOWN_ROLES.find((r) => r.value === role);
  if (wk) return wk.hint;
  if (SERVICE_ROLES.includes(role as (typeof SERVICE_ROLES)[number])) {
    return `Routes to the ${role} service.`;
  }
  if (role) return `Treated as literal service id "${role}".`;
  return null;
}

function rowsFromValue(value: Record<string, string>): Row[] {
  return Object.entries(value).map(([role, host], i) => ({
    role,
    host,
    k: `${role}-${i}`,
  }));
}

function valueFromRows(rows: Row[]): Record<string, string> {
  // Last-write-wins on duplicate roles; empty hosts are dropped.
  // (The validator surfaces both as errors before save, but we keep
  // the projection lossless-ish for live preview purposes.)
  const out: Record<string, string> = {};
  for (const r of rows) {
    const role = r.role.trim();
    const host = r.host.trim();
    if (!role || !host) continue;
    out[role] = host;
  }
  return out;
}

function validateRows(rows: Row[], gatewayHost?: string): Map<string, RowError> {
  const errors = new Map<string, RowError>();
  const roleCounts = new Map<string, number>();
  const hostCounts = new Map<string, number>();
  for (const r of rows) {
    const role = r.role.trim();
    const host = r.host.trim();
    if (role) roleCounts.set(role, (roleCounts.get(role) ?? 0) + 1);
    if (host) hostCounts.set(host, (hostCounts.get(host) ?? 0) + 1);
  }
  for (const r of rows) {
    const role = r.role.trim();
    const host = r.host.trim();
    const e: RowError = {};
    if (host && !role) {
      e.role = "Pick a role for this hostname.";
    }
    if (role && (roleCounts.get(role) ?? 0) > 1) {
      e.role = `Role "${role}" is set on more than one row — keep only one.`;
    }
    if (host && !looksLikeHost(host)) {
      e.host = "Looks invalid — fully-qualified hostname required (e.g. jf.example.com).";
    }
    if (host && (hostCounts.get(host) ?? 0) > 1) {
      e.host = `Hostname "${host}" is mapped to more than one role.`;
    }
    if (host && gatewayHost && host === gatewayHost.trim()) {
      e.hostWarning =
        "Same as the gateway hostname — direct_hosts only matters when it differs.";
    }
    errors.set(r.k, e);
  }
  return errors;
}

export function DirectHostsEditor({
  value,
  onChange,
  gatewayHost,
}: DirectHostsEditorProps) {
  const baseId = useId();

  // Keep row state in the parent (RoutingEditor) — but we materialise
  // a row-array view for rendering. Each onChange computes a new
  // Record from the post-edit row array and bubbles up. The parent
  // re-passes value, so re-keying is the parent's job too.
  const rows = useMemo(() => rowsFromValue(value), [value]);

  // Working copy: reuse the rows derived from value, but allow the
  // operator to add an empty row that doesn't yet have a hostname.
  // We track those purely-empty rows in a separate "drafts" array
  // bolted onto the rendering, since a Record can't represent them.
  // To keep this self-contained we represent drafts as rows with
  // `host: ""` and a synthetic role placeholder. A row with both
  // empty role and empty host is a fresh draft.
  // The simpler shape: parent always passes the full record. Drafts
  // are owned by the parent. Add-row clicks bubble up through
  // onAddDraft. To keep the API minimal we let the Editor itself
  // own a small internal "drafts" slot and merge for render.
  // … Simplification: use a single `internalRows` array.

  // Internal-only drafts that have empty role+host. They live until
  // the operator types something; once both fields are non-empty
  // they merge into `value` and become real entries.
  const errors = useMemo(() => validateRows(rows, gatewayHost), [rows, gatewayHost]);

  const updateRowRole = (k: string, nextRole: string) => {
    const next: Row[] = rows.map((r) => (r.k === k ? { ...r, role: nextRole } : r));
    onChange(valueFromRows(next));
  };
  const updateRowHost = (k: string, nextHost: string) => {
    const next: Row[] = rows.map((r) =>
      r.k === k ? { ...r, host: nextHost.trim() } : r,
    );
    onChange(valueFromRows(next));
  };
  const removeRow = (k: string) => {
    const next: Row[] = rows.filter((r) => r.k !== k);
    onChange(valueFromRows(next));
  };
  const addRow = () => {
    // Allocate an unused well-known role first; fall back to the
    // first service id; fall back to a unique placeholder.
    const usedRoles = new Set(rows.map((r) => r.role));
    const candidates: readonly string[] = [
      ...WELL_KNOWN_ROLES.map((r) => r.value),
      ...SERVICE_ROLES,
    ];
    const next = candidates.find((c) => !usedRoles.has(c));
    if (!next) {
      // Fallback — synth a unique role so the validator catches it
      // and the operator can rename.
      let i = 1;
      while (usedRoles.has(`role_${i}`)) i++;
      onChange({ ...value, [`role_${i}`]: "" });
      return;
    }
    onChange({ ...value, [next]: "" });
  };

  return (
    <div
      className="flex flex-col gap-3"
      data-testid="direct-hosts-editor"
    >
      <div className="flex flex-col gap-1">
        <Label className="text-sm font-medium">Direct host overrides</Label>
        <p className="text-xs text-fg-muted">
          Map a role to a custom hostname (e.g. <span className="font-mono">jf.example.com</span> for
          the media server). The controller writes matching Envoy vhosts and K8s
          Ingress rules — you don't define the route, host, and service
          separately. Empty rows are ignored.
        </p>
      </div>

      {rows.length === 0 ? (
        <div
          className="rounded-md border border-dashed border-border bg-bg-2/40 p-3 text-xs text-fg-muted"
          data-testid="direct-hosts-empty"
        >
          No direct host overrides. Add one to route a service through a
          custom hostname instead of the convention{" "}
          <span className="font-mono">{"<svc>.<sub>.<base>"}</span>.
        </div>
      ) : (
        <div
          className="flex flex-col divide-y divide-border rounded-md border border-border"
          data-testid="direct-hosts-rows"
          role="table"
          aria-label="Direct host overrides"
        >
          <div
            className="grid grid-cols-12 gap-2 bg-bg-2/40 px-3 py-2 text-xs font-medium text-fg-muted"
            role="row"
          >
            <div className="col-span-4" role="columnheader">Role</div>
            <div className="col-span-7" role="columnheader">Hostname</div>
            <div className="col-span-1" role="columnheader" aria-label="Actions" />
          </div>
          {rows.map((row, idx) => {
            const err = errors.get(row.k) ?? {};
            const selectValue = isWellKnownRole(row.role) || SERVICE_ROLES.includes(row.role as (typeof SERVICE_ROLES)[number])
              ? row.role
              : CUSTOM_ROLE;
            const isCustom = selectValue === CUSTOM_ROLE;
            return (
              <div
                key={row.k}
                className="grid grid-cols-12 items-start gap-2 px-3 py-3"
                data-testid={`direct-host-row-${idx}`}
                role="row"
              >
                <div className="col-span-4 flex flex-col gap-1" role="cell">
                  <Select
                    value={selectValue}
                    onValueChange={(v) => {
                      if (v === CUSTOM_ROLE) {
                        // Switching to custom — start with empty role
                        // so the user explicitly types one.
                        updateRowRole(row.k, "");
                      } else {
                        updateRowRole(row.k, v);
                      }
                    }}
                  >
                    <SelectTrigger
                      id={`${baseId}-${idx}-role`}
                      data-testid={`direct-host-row-${idx}-role`}
                      aria-label={`Role for row ${idx + 1}`}
                    >
                      <SelectValue placeholder="Pick a role…" />
                    </SelectTrigger>
                    <SelectContent>
                      {WELL_KNOWN_ROLES.map((r) => (
                        <SelectItem key={r.value} value={r.value}>
                          {r.label}
                        </SelectItem>
                      ))}
                      {SERVICE_ROLES.map((r) => (
                        <SelectItem key={r} value={r}>
                          {r}
                        </SelectItem>
                      ))}
                      <SelectItem value={CUSTOM_ROLE}>Custom…</SelectItem>
                    </SelectContent>
                  </Select>
                  {isCustom ? (
                    <Input
                      value={row.role}
                      onChange={(e) => updateRowRole(row.k, e.target.value.trim())}
                      placeholder="custom-role"
                      data-testid={`direct-host-row-${idx}-role-custom`}
                      aria-label={`Custom role for row ${idx + 1}`}
                    />
                  ) : null}
                  <p className="text-[11px] text-fg-muted">
                    {describeRole(row.role) ?? "Pick a role above."}
                  </p>
                  {err.role ? (
                    <p
                      className="flex items-start gap-1 text-[11px] text-danger"
                      role="alert"
                      data-testid={`direct-host-row-${idx}-role-error`}
                    >
                      <AlertCircle aria-hidden className="mt-px size-3 shrink-0" />
                      <span>{err.role}</span>
                    </p>
                  ) : null}
                </div>
                <div className="col-span-7 flex flex-col gap-1" role="cell">
                  <Input
                    value={row.host}
                    onChange={(e) => updateRowHost(row.k, e.target.value)}
                    placeholder="jf.example.com"
                    data-testid={`direct-host-row-${idx}-host`}
                    aria-label={`Hostname for row ${idx + 1}`}
                    aria-invalid={err.host ? true : undefined}
                  />
                  {err.host ? (
                    <p
                      className="flex items-start gap-1 text-[11px] text-danger"
                      role="alert"
                      data-testid={`direct-host-row-${idx}-host-error`}
                    >
                      <AlertCircle aria-hidden className="mt-px size-3 shrink-0" />
                      <span>{err.host}</span>
                    </p>
                  ) : err.hostWarning ? (
                    <p
                      className="flex items-start gap-1 text-[11px] text-warning"
                      data-testid={`direct-host-row-${idx}-host-warning`}
                    >
                      <AlertCircle aria-hidden className="mt-px size-3 shrink-0" />
                      <span>{err.hostWarning}</span>
                    </p>
                  ) : row.host && row.role ? (
                    <p className="text-[11px] text-fg-muted">
                      Will resolve to{" "}
                      <span className="font-mono">https://{row.host}</span>.
                    </p>
                  ) : null}
                </div>
                <div className="col-span-1 flex justify-end" role="cell">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => removeRow(row.k)}
                    aria-label={`Remove row ${idx + 1}`}
                    data-testid={`direct-host-row-${idx}-remove`}
                  >
                    <Trash2 aria-hidden className="size-4" />
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div>
        <Button
          variant="secondary"
          size="sm"
          onClick={addRow}
          data-testid="direct-hosts-add"
        >
          <Plus aria-hidden className="size-4" />
          Add direct host
        </Button>
      </div>
    </div>
  );
}

/**
 * Pure helper exposed for tests (and for callers that want to
 * pre-validate before submit). Returns a flat error string array
 * suitable for blocking submit at the form level. An empty array
 * means the whole table is valid.
 */
export function collectDirectHostErrors(
  value: Record<string, string>,
  gatewayHost?: string,
): readonly string[] {
  const rows = rowsFromValue(value);
  const errs = validateRows(rows, gatewayHost);
  const flat: string[] = [];
  for (const e of errs.values()) {
    if (e.role) flat.push(e.role);
    if (e.host) flat.push(e.host);
  }
  // Dedup adjacent identical strings (the duplicate-role/host cases
  // emit the same message on both rows).
  return Array.from(new Set(flat));
}
