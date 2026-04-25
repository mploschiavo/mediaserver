import { useEffect, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useLogLevel, useSetLogLevel, type LogLevelInput } from "./hooks";

type LogLevel = LogLevelInput["level"];

const LEVELS: ReadonlyArray<{ value: LogLevel; label: string }> = [
  { value: "debug", label: "Debug" },
  { value: "info", label: "Info" },
  { value: "warn", label: "Warn" },
  { value: "error", label: "Error" },
];

function levelVariant(
  l: string | undefined,
): "default" | "info" | "warning" | "danger" {
  switch ((l ?? "").toLowerCase()) {
    case "debug":
      return "info";
    case "warn":
    case "warning":
      return "warning";
    case "error":
      return "danger";
    default:
      return "default";
  }
}

function isLogLevel(v: string): v is LogLevel {
  return v === "debug" || v === "info" || v === "warn" || v === "error";
}

function errMsg(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}

/**
 * Runtime log-level setter. Reads `/api/log-level`, writes a
 * normalized `{ level }` body to the same path. Switching to
 * debug surfaces a warning so operators understand the volume
 * trade-off.
 */
export function LogLevelCard() {
  const current = useLogLevel();
  const apply = useSetLogLevel();

  const initial =
    typeof current.data?.level === "string" && isLogLevel(current.data.level)
      ? current.data.level
      : "info";
  const [draft, setDraft] = useState<LogLevel>(initial);

  useEffect(() => {
    setDraft(initial);
  }, [initial]);

  const handleApply = () => {
    if (apply.isPending) return;
    apply.mutate(
      { level: draft },
      {
        onSuccess: () => toast.success(`Log level set to ${draft}`),
        onError: (err) => toast.error(errMsg(err, "Failed to set log level")),
      },
    );
  };

  return (
    <Card data-testid="log-level-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          Log level
          {current.data?.level ? (
            <Badge
              variant={levelVariant(current.data.level)}
              data-testid="log-level-current"
            >
              {String(current.data.level)}
            </Badge>
          ) : null}
        </CardTitle>
        <CardDescription>
          Runtime verbosity. Applies immediately to the controller.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {current.isLoading ? (
          <Skeleton className="h-10 w-full" data-testid="log-level-loading" />
        ) : current.error ? (
          <div
            role="alert"
            data-testid="log-level-error"
            className="text-sm text-danger"
          >
            {current.error.message}
          </div>
        ) : (
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="log-level-select">Level</Label>
            <Select
              value={draft}
              onValueChange={(v) => {
                if (isLogLevel(v)) setDraft(v);
              }}
            >
              <SelectTrigger id="log-level-select" data-testid="log-level-select">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {LEVELS.map((l) => (
                  <SelectItem key={l.value} value={l.value}>
                    {l.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
        {draft === "debug" ? (
          <div
            role="note"
            data-testid="log-level-debug-warning"
            className="flex items-start gap-2 rounded-md border border-[color-mix(in_oklab,var(--color-warning)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-warning)_12%,transparent)] p-2 text-warning"
          >
            <AlertTriangle aria-hidden className="mt-0.5 size-4 shrink-0" />
            <div className="text-xs">
              Debug increases log volume substantially. Plan to revert when
              the investigation finishes.
            </div>
          </div>
        ) : null}
        <div className="flex items-center justify-end">
          <Button
            variant="primary"
            onClick={handleApply}
            disabled={current.isLoading || apply.isPending}
            loading={apply.isPending}
            data-testid="log-level-apply"
          >
            Apply
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
