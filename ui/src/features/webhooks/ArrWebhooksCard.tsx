import { useMemo } from "react";
import { Webhook } from "lucide-react";
import { EmptyState } from "@/components/layout/EmptyState";
import {
  ResponsiveTable,
  type ResponsiveTableColumn,
} from "@/components/layout/ResponsiveTable";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useArrWebhooks, type ArrWebhookEntry } from "./hooks";

function timeAgo(iso?: string): string {
  if (!iso) return "never";
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return "never";
  const ms = Date.now() - t;
  if (ms < 0) return "never";
  const m = Math.floor(ms / 60_000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

function normalise(data: unknown): ArrWebhookEntry[] {
  if (!data || typeof data !== "object") return [];
  const rec = data as Record<string, unknown>;
  // Prefer `services` (current shape) then `webhooks` (legacy alias),
  // and finally fall back to interpreting top-level service keys.
  const candidate = (rec.services ?? rec.webhooks) as unknown;
  if (Array.isArray(candidate)) {
    return candidate
      .filter((c): c is Record<string, unknown> => !!c && typeof c === "object")
      .map((c) => ({
        service: typeof c.service === "string" ? c.service : "unknown",
        configured: c.configured === true,
        url: typeof c.url === "string" ? c.url : undefined,
        last_delivery:
          typeof c.last_delivery === "string" ? c.last_delivery : undefined,
      }));
  }
  // Fall-through: object keyed by service name.
  return Object.entries(rec)
    .filter(([, v]) => v && typeof v === "object")
    .map(([service, v]) => {
      const entry = v as Record<string, unknown>;
      return {
        service,
        configured: entry.configured === true,
        url: typeof entry.url === "string" ? entry.url : undefined,
        last_delivery:
          typeof entry.last_delivery === "string"
            ? entry.last_delivery
            : undefined,
      };
    });
}

/**
 * Read-only summary card that surfaces the controller-managed Arr
 * webhook configuration (Sonarr/Radarr/Lidarr/Readarr). The
 * controller owns these — the operator can't add/remove from the
 * UI; this card is purely informational.
 */
export function ArrWebhooksCard() {
  const arr = useArrWebhooks();
  const rows = useMemo<ArrWebhookEntry[]>(
    () => normalise(arr.data),
    [arr.data],
  );

  const columns: ResponsiveTableColumn<ArrWebhookEntry>[] = [
    {
      id: "service",
      header: "Service",
      cell: (row) => (
        <span className="font-medium capitalize text-fg">{row.service}</span>
      ),
    },
    {
      id: "configured",
      header: "Configured",
      cell: (row) =>
        row.configured ? (
          <Badge variant="success">configured</Badge>
        ) : (
          <Badge variant="warning">missing</Badge>
        ),
    },
    {
      id: "url",
      header: "URL",
      cell: (row) =>
        row.url ? (
          <span
            className="block max-w-[28ch] truncate font-mono text-xs text-fg-muted"
            title={row.url}
          >
            {row.url}
          </span>
        ) : (
          <span className="text-fg-faint">—</span>
        ),
    },
    {
      id: "last-delivery",
      header: "Last delivery",
      cell: (row) => (
        <span className="tabular-nums text-fg-muted">
          {timeAgo(row.last_delivery)}
        </span>
      ),
    },
  ];

  return (
    <Card data-testid="arr-webhooks-card">
      <CardHeader>
        <CardTitle>*arr integration webhooks</CardTitle>
        <CardDescription>
          The controller registers and manages these on each Servarr
          service. Read-only here — adjust via the controller config.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {arr.isLoading ? (
          <div className="space-y-2" data-testid="arr-webhooks-loading">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : arr.error ? (
          <div
            role="alert"
            data-testid="arr-webhooks-error"
            className="text-sm text-danger"
          >
            {arr.error.message}
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            icon={Webhook}
            title="No *arr services discovered"
            description="The controller didn't return any Sonarr/Radarr/Lidarr/Readarr services to manage."
          />
        ) : (
          <ResponsiveTable
            rows={rows}
            rowKey={(r) => r.service}
            columns={columns}
            card={(row) => (
              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between">
                  <span className="font-medium capitalize text-fg">
                    {row.service}
                  </span>
                  {row.configured ? (
                    <Badge variant="success">configured</Badge>
                  ) : (
                    <Badge variant="warning">missing</Badge>
                  )}
                </div>
                <span
                  className="block truncate font-mono text-xs text-fg-muted"
                  title={row.url ?? ""}
                >
                  {row.url ?? "—"}
                </span>
                <span className="text-xs tabular-nums text-fg-muted">
                  fired {timeAgo(row.last_delivery)}
                </span>
              </div>
            )}
          />
        )}
      </CardContent>
    </Card>
  );
}
