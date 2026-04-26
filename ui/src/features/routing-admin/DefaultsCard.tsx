import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useRoutingV2 } from "./hooks";

/**
 * Read-only view of `routing.defaults` — the inherit-this-everywhere
 * knobs (websocket on/off, default auth gate, timeout, body limit,
 * global response headers). Per-host overrides win; this card pins
 * the baseline for any host that hasn't customised.
 *
 * Card 6 from the design doc; PR-6 ships read-only, PR-6.5 will
 * make it editable.
 */
export function DefaultsCard() {
  const q = useRoutingV2();

  if (q.isLoading) {
    return (
      <Card data-testid="defaults-card-loading">
        <CardHeader>
          <CardTitle>Defaults</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-24 w-full rounded-md" />
        </CardContent>
      </Card>
    );
  }

  if (q.error || !q.data) {
    return (
      <Card data-testid="defaults-card-error" role="alert">
        <CardHeader>
          <CardTitle>Defaults</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-danger">
            Couldn't load defaults:{" "}
            {q.error ? (q.error as Error).message : "no data"}
          </p>
        </CardContent>
      </Card>
    );
  }

  const d = q.data.config.defaults ?? {};
  const responseHeaders = (d as { headers?: { response_set?: Record<string, string> } }).headers
    ?.response_set ?? {};
  const headerCount = Object.keys(responseHeaders).length;

  return (
    <Card data-testid="defaults-card">
      <CardHeader>
        <CardTitle>Defaults</CardTitle>
        <CardDescription>
          Inherited knobs that apply to every host unless explicitly
          overridden. Per-host overrides surface in the Hostnames
          editor.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-4">
          <DefaultRow
            label="WebSocket"
            value={d.websocket ? "Allowed" : "Off"}
            tone={d.websocket ? "info" : "muted"}
            testid="defaults-websocket"
          />
          <DefaultRow
            label="Auth gate"
            value={d.auth?.gate ?? "none"}
            tone={d.auth?.gate === "required" ? "warning" : "muted"}
            testid="defaults-auth"
          />
          <DefaultRow
            label="Timeout"
            value={d.timeout_seconds ? `${d.timeout_seconds}s` : "Envoy default"}
            tone="muted"
            testid="defaults-timeout"
          />
          <DefaultRow
            label="Body limit"
            value={d.body_limit_mb ? `${d.body_limit_mb} MB` : "—"}
            tone="muted"
            testid="defaults-body-limit"
          />
        </dl>

        {headerCount > 0 ? (
          <div className="mt-4 flex flex-col gap-1">
            <div className="text-xs uppercase tracking-wide text-fg-faint">
              Global response headers ({headerCount})
            </div>
            <ul className="flex flex-wrap gap-1.5" data-testid="defaults-headers">
              {Object.entries(responseHeaders).map(([k, v]) => (
                <li key={k}>
                  <code className="rounded bg-bg-2 px-1.5 py-0.5 text-xs text-fg-muted">
                    {k}: {v}
                  </code>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function DefaultRow({
  label,
  value,
  tone,
  testid,
}: {
  label: string;
  value: string;
  tone: "info" | "warning" | "muted";
  testid: string;
}) {
  return (
    <div className="flex flex-col gap-0.5" data-testid={testid}>
      <dt className="text-xs uppercase tracking-wide text-fg-faint">{label}</dt>
      <dd>
        <Badge variant="outline" data-tone={tone}>
          {value}
        </Badge>
      </dd>
    </div>
  );
}
