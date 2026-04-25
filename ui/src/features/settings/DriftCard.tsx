import { GitCompareArrows } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { asArray } from "@/lib/coerce";
import { useConfigDrift, type DriftEntry } from "./hooks";

function severityVariant(
  s: string | undefined,
): "default" | "warning" | "danger" | "info" {
  switch ((s ?? "").toLowerCase()) {
    case "error":
    case "critical":
      return "danger";
    case "warn":
    case "warning":
      return "warning";
    case "info":
      return "info";
    default:
      return "default";
  }
}

function entryKey(d: DriftEntry, idx: number): string {
  return String(d.key ?? d.path ?? idx);
}

function fmt(v: unknown): string {
  if (v === undefined || v === null) return "—";
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

/**
 * Drift card — surfaces every key whose live value diverged from
 * the profile snapshot. The "Reconcile" button kicks over to the
 * `/ops` route which owns the actual reconcile mutation; the
 * settings surface only describes the gap.
 */
export function DriftCard() {
  const drift = useConfigDrift();
  const entries = asArray<DriftEntry>(
    drift.data?.drift ?? drift.data?.entries,
  );

  return (
    <Card data-testid="drift-card">
      <CardHeader className="flex-row items-start justify-between gap-3 sm:flex-row sm:items-center">
        <div className="flex flex-col gap-1.5">
          <CardTitle className="flex items-center gap-2">
            <GitCompareArrows aria-hidden className="size-4 text-fg-muted" />
            Configuration drift
          </CardTitle>
          <CardDescription>
            Keys where the live state has diverged from the profile.
          </CardDescription>
        </div>
        <Button asChild variant="secondary" data-testid="drift-reconcile-link">
          <a href="/ops">Reconcile</a>
        </Button>
      </CardHeader>
      <CardContent className="p-0">
        {drift.isLoading ? (
          <div className="space-y-2 p-6" data-testid="drift-card-loading">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : drift.error ? (
          <div
            role="alert"
            data-testid="drift-card-error"
            className="px-6 py-4 text-sm text-danger"
          >
            {drift.error.message}
          </div>
        ) : entries.length === 0 ? (
          <p
            className="px-6 py-4 text-sm text-fg-muted"
            data-testid="drift-card-empty"
          >
            No drift — profile and live state match.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Key</TableHead>
                <TableHead>Profile</TableHead>
                <TableHead>Live</TableHead>
                <TableHead className="text-right">Severity</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {entries.map((d, i) => {
                const k = entryKey(d, i);
                return (
                  <TableRow key={k} data-testid={`drift-row-${k}`}>
                    <TableCell className="font-mono text-xs text-fg">
                      {k}
                    </TableCell>
                    <TableCell className="font-mono text-xs text-fg-muted">
                      {fmt(d.profile_value)}
                    </TableCell>
                    <TableCell className="font-mono text-xs text-fg-muted">
                      {fmt(d.live_value)}
                    </TableCell>
                    <TableCell className="text-right">
                      <Badge variant={severityVariant(d.severity)}>
                        {d.severity ?? "info"}
                      </Badge>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
