import { Activity, Copy } from "lucide-react";
import { toast } from "sonner";
import { asArray } from "@/lib/coerce";
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
import { formatRelative } from "@/features/media-integrity/format";
import { useEpgHealth } from "./hooks";

async function writeClipboard(text: string): Promise<boolean> {
  try {
    if (
      typeof navigator !== "undefined" &&
      navigator.clipboard &&
      typeof navigator.clipboard.writeText === "function"
    ) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through
  }
  return false;
}

/** Read the canonical `ok` boolean from the loose payload. The
 *  controller has shipped both `{ok: true}` and `{status: "ok"}`. */
function isHealthy(data: { ok?: boolean; status?: string }): boolean {
  if (typeof data.ok === "boolean") return data.ok;
  const s = (data.status ?? "").toLowerCase();
  if (!s) return false;
  return s === "ok" || s === "healthy" || s === "success";
}

/** Coerce `errors` into a count regardless of payload shape. */
function errorCount(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (Array.isArray(value)) return value.length;
  return 0;
}

/**
 * Compact EPG health chip — shows the last fetch time, success/fail
 * badge, error count, and a click-to-copy list of any channels missing
 * guide data.
 */
export function EpgHealthCard() {
  const health = useEpgHealth();

  const handleCopy = async (channel: string) => {
    const ok = await writeClipboard(channel);
    if (ok) {
      toast.success(`Copied ${channel}`);
    } else {
      toast.error("Clipboard unavailable");
    }
  };

  return (
    <Card data-testid="epg-health-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Activity aria-hidden className="size-4 text-fg-muted" />
          EPG health
        </CardTitle>
        <CardDescription>
          Status of the last guide fetch — any errors and channels
          missing programme data.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {health.isLoading ? (
          <div className="space-y-2" data-testid="epg-health-loading">
            <Skeleton className="h-5 w-32" />
            <Skeleton className="h-4 w-48" />
          </div>
        ) : health.error ? (
          <p
            role="alert"
            className="text-sm text-danger"
            data-testid="epg-health-error"
          >
            {health.error.message}
          </p>
        ) : !health.data ? (
          <p
            className="text-sm text-fg-muted"
            data-testid="epg-health-empty"
          >
            No EPG runs reported yet.
          </p>
        ) : (
          <HealthBody data={health.data} onCopy={handleCopy} />
        )}
      </CardContent>
    </Card>
  );
}

interface HealthBodyProps {
  data: NonNullable<ReturnType<typeof useEpgHealth>["data"]>;
  onCopy: (channel: string) => void;
}

function HealthBody({ data, onCopy }: HealthBodyProps) {
  const healthy = isHealthy(data);
  const errors = errorCount(data.errors);
  const missing = asArray<string>(data.missing_channels);

  return (
    <div className="flex flex-col gap-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge
          variant={healthy ? "success" : "danger"}
          data-testid="epg-health-status"
        >
          {healthy ? "healthy" : "failing"}
        </Badge>
        <span className="tabular-nums text-fg-muted">
          last run {formatRelative(data.last_run ?? "")}
        </span>
        {errors > 0 ? (
          <Badge variant="danger" data-testid="epg-health-errors">
            {errors} error{errors === 1 ? "" : "s"}
          </Badge>
        ) : null}
      </div>
      {missing.length > 0 ? (
        <div className="flex flex-col gap-1">
          <p className="text-xs font-medium text-fg-muted">
            Missing channels ({missing.length})
          </p>
          <ul
            className="flex flex-col gap-1 rounded-md border border-border bg-bg-1 p-2 text-xs"
            data-testid="epg-missing-channels"
          >
            {missing.map((ch) => (
              <li key={ch} className="flex items-center justify-between gap-2">
                <span className="truncate font-mono">{ch}</span>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => onCopy(ch)}
                  aria-label={`Copy ${ch}`}
                  data-testid={`epg-copy-${ch}`}
                >
                  <Copy aria-hidden /> copy
                </Button>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="text-xs text-fg-muted">
          All channels have programme data.
        </p>
      )}
    </div>
  );
}
