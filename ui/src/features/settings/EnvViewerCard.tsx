import { useMemo, useState } from "react";
import { Download, Lock } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { asArray, asObjectMap } from "@/lib/coerce";
import {
  isSensitiveKey,
  useEffectiveEnv,
  type EnvEntry,
  type EnvResponse,
} from "./hooks";

// ---------------------------------------------------------------------------
// Effective env normalisation.
// ---------------------------------------------------------------------------

interface NormalizedEnv {
  key: string;
  value: string;
  sensitive: boolean;
  /** Pre-categorised bucket for the grouped display. */
  category: "deployment" | "services" | "api-keys" | "other";
}

const DEPLOYMENT_KEYS = new Set([
  "STACK_MODE",
  "NAMESPACE",
  "GATEWAY_HOST",
  "BASE_DOMAIN",
  "MEDIA_STACK_NAMESPACE",
  "MEDIA_STACK_PROJECT",
]);

const SERVICE_KEY_RE =
  /^(SONARR|RADARR|LIDARR|READARR|PROWLARR|JELLYFIN|JELLYSEERR|BAZARR|TAUTULLI|QBITTORRENT|SABNZBD|HOMEPAGE|ENVOY|CONTROLLER)_/;

function categorize(key: string): NormalizedEnv["category"] {
  if (isSensitiveKey(key)) return "api-keys";
  if (DEPLOYMENT_KEYS.has(key.toUpperCase())) return "deployment";
  if (SERVICE_KEY_RE.test(key.toUpperCase())) return "services";
  return "other";
}

/**
 * Coerce the live `/api/env` payload to a flat list. Accepts both
 * the documented `{ env: [{key, value}] }` shape and the older
 * `{ values: { KEY: "value" } }` map. Defends against a payload
 * shaped as `additionalProperties: true` via `asObjectMap`.
 */
export function normalizeEnv(data: EnvResponse | undefined): NormalizedEnv[] {
  if (!data) return [];
  const list = asArray<EnvEntry>(data.env);
  if (list.length > 0) {
    return list.map((e) => {
      const key = String(e.key ?? e.name ?? "");
      const value = typeof e.value === "string" ? e.value : "";
      return {
        key,
        value,
        sensitive: isSensitiveKey(key),
        category: categorize(key),
      };
    });
  }
  // Fallback: { values: { KEY: "value" } } — older controller shape.
  const values = asObjectMap(data.values);
  if (Object.keys(values).length > 0) {
    return Object.entries(values).map(([key, raw]) => ({
      key,
      value: typeof raw === "string" ? raw : String(raw ?? ""),
      sensitive: isSensitiveKey(key),
      category: categorize(key),
    }));
  }
  return [];
}

interface CategoryGroup {
  id: NormalizedEnv["category"];
  label: string;
  description: string;
  rows: NormalizedEnv[];
}

const CATEGORY_ORDER: ReadonlyArray<{
  id: NormalizedEnv["category"];
  label: string;
  description: string;
}> = [
  {
    id: "deployment",
    label: "Deployment",
    description: "stack mode, namespace, gateway",
  },
  {
    id: "services",
    label: "Services",
    description: "host:port for each known service",
  },
  {
    id: "api-keys",
    label: "API keys",
    description: "presence-only (values stay masked)",
  },
  {
    id: "other",
    label: "Other",
    description: "everything else the controller sees",
  },
];

function groupByCategory(rows: NormalizedEnv[]): CategoryGroup[] {
  const buckets = new Map<NormalizedEnv["category"], NormalizedEnv[]>();
  for (const r of rows) {
    const arr = buckets.get(r.category);
    if (arr) arr.push(r);
    else buckets.set(r.category, [r]);
  }
  return CATEGORY_ORDER.map((c) => ({
    id: c.id,
    label: c.label,
    description: c.description,
    rows: (buckets.get(c.id) ?? []).slice().sort((a, b) =>
      a.key.localeCompare(b.key),
    ),
  })).filter((g) => g.rows.length > 0);
}

/**
 * Build a `.env`-style export. When `masked=true`, sensitive values
 * are written as `KEY=` so the file is safe to share. When false,
 * the raw value is included — caller's responsibility to gate.
 */
export function buildEnvFile(
  rows: readonly NormalizedEnv[],
  masked: boolean,
): string {
  const lines: string[] = [];
  for (const group of groupByCategory(rows.slice())) {
    lines.push(`# ${group.label}`);
    for (const r of group.rows) {
      const value = r.sensitive && masked ? "" : r.value;
      // Quote any value that contains whitespace; otherwise emit
      // bare. Keys are validated by the controller, so we don't
      // re-validate here.
      const needsQuotes = /[\s"\\]/.test(value);
      const safe = needsQuotes
        ? `"${value.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`
        : value;
      lines.push(`${r.key}=${safe}`);
    }
    lines.push("");
  }
  return lines.join("\n").trim() + "\n";
}

function downloadText(filename: string, body: string): void {
  // Minimal, dependency-free download — the bundle budget rules out
  // file-saver. Render a transient anchor and click it. The global
  // `URL.createObjectURL` / `URL.revokeObjectURL` are looked up off
  // the constructor at call-time so tests can stub them in via
  // simple property assignment (happy-dom's URL doesn't always
  // expose `revokeObjectURL` as a static method).
  const blob = new Blob([body], { type: "text/plain;charset=utf-8" });
  const create = (URL as unknown as {
    createObjectURL?: (blob: Blob) => string;
  }).createObjectURL;
  const revoke = (URL as unknown as {
    revokeObjectURL?: (url: string) => void;
  }).revokeObjectURL;
  if (typeof create !== "function") {
    throw new Error("URL.createObjectURL is unavailable in this environment");
  }
  const url = create(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    if (typeof revoke === "function") {
      revoke(url);
    }
  }
}

// ---------------------------------------------------------------------------
// "Effective environment" card.
// ---------------------------------------------------------------------------

/**
 * Read-only effective-env card. Groups the live `/api/env` payload
 * into Deployment / Services / API keys / Other; renders each row
 * with sensitive values always masked (the editable editor card
 * is the only surface with reveal capability — even there, the
 * raw value never makes it to the console). Provides a `.env`
 * export with both masked and unmasked options.
 *
 * Pairs with `EnvVarsEditorCard` ("Bootstrap variables") so the
 * operator can see "what's currently applied" vs "what I'm allowed
 * to change" at a glance.
 */
export function EnvViewerCard() {
  const env = useEffectiveEnv();
  const [filter, setFilter] = useState<string>("");

  const rows = useMemo(() => normalizeEnv(env.data), [env.data]);
  const filteredRows = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(
      (r) =>
        r.key.toLowerCase().includes(q) ||
        // Sensitive values are never matched against the filter so
        // the operator can't unmask them via search behaviour.
        (!r.sensitive && r.value.toLowerCase().includes(q)),
    );
  }, [filter, rows]);
  const groups = useMemo(() => groupByCategory(filteredRows), [filteredRows]);

  const handleExport = (masked: boolean) => {
    if (rows.length === 0) {
      toast.error("Nothing to export");
      return;
    }
    try {
      const body = buildEnvFile(rows, masked);
      const ts = new Date().toISOString().replace(/[:.]/g, "-");
      const suffix = masked ? "masked" : "unmasked";
      downloadText(`media-stack-${suffix}-${ts}.env`, body);
      toast.success(`Exported ${rows.length} variables`);
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Export failed",
      );
    }
  };

  return (
    <Card data-testid="env-viewer-card">
      <CardHeader className="flex-row items-start justify-between gap-3 sm:items-center">
        <div className="flex flex-col gap-1.5">
          <CardTitle>Effective environment</CardTitle>
          <CardDescription>
            What the controller sees. Sensitive values are always masked.
          </CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            size="sm"
            variant="secondary"
            onClick={() => handleExport(true)}
            data-testid="env-export-masked"
            disabled={rows.length === 0}
          >
            <Download aria-hidden className="size-3.5" />
            Export masked
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => handleExport(false)}
            data-testid="env-export-unmasked"
            disabled={rows.length === 0}
            title="Includes secret values — handle with care."
          >
            Unmasked
          </Button>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 p-6 pt-0">
        <Input
          placeholder="Filter by key…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          data-testid="env-viewer-filter"
          aria-label="Filter env vars"
        />
        {env.isLoading ? (
          <div className="space-y-2" data-testid="env-viewer-loading">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : env.error ? (
          <div
            role="alert"
            data-testid="env-viewer-error"
            className="text-sm text-danger"
          >
            {env.error.message}
          </div>
        ) : groups.length === 0 ? (
          <p className="text-sm text-fg-muted" data-testid="env-viewer-empty">
            {rows.length === 0 ? "No environment data." : "No matches."}
          </p>
        ) : (
          <div className="flex flex-col gap-4" data-testid="env-viewer-groups">
            {groups.map((g) => (
              <section
                key={g.id}
                aria-labelledby={`env-group-${g.id}-title`}
                data-testid={`env-group-${g.id}`}
                className="rounded-md border border-border bg-bg-1"
              >
                <header className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
                  <div className="flex flex-col">
                    <h3
                      id={`env-group-${g.id}-title`}
                      className="text-sm font-semibold text-fg"
                    >
                      {g.label}
                    </h3>
                    <p className="text-xs text-fg-muted">{g.description}</p>
                  </div>
                  <Badge variant="outline">
                    {g.rows.length} {g.rows.length === 1 ? "var" : "vars"}
                  </Badge>
                </header>
                <ul className="flex flex-col">
                  {g.rows.map((r) => (
                    <li
                      key={r.key}
                      data-testid={`env-row-${r.key}`}
                      className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-1.5 text-xs last:border-b-0"
                    >
                      <span className="font-mono text-fg">{r.key}</span>
                      <span className="flex items-center gap-2 font-mono text-fg-muted">
                        {r.sensitive ? (
                          <>
                            <Lock
                              aria-hidden
                              className="size-3 text-fg-muted"
                            />
                            <Badge
                              variant={
                                r.value.length > 0 ? "success" : "outline"
                              }
                              data-testid={`env-isset-${r.key}`}
                            >
                              {r.value.length > 0 ? "set" : "not set"}
                            </Badge>
                          </>
                        ) : r.value ? (
                          <span data-testid={`env-value-${r.key}`}>
                            {r.value}
                          </span>
                        ) : (
                          <span className="text-fg-muted">—</span>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
