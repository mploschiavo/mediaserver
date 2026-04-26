import { Globe, Lock } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useRoutingV2, type RoutingV2Exposure } from "./hooks";

/**
 * Read-only view of `routing.exposure` for PR-4 — the toggle becomes
 * editable in PR-5 once the POST /api/routing/v2 + apply path lands.
 *
 * Shows: enabled flag, the active binding mode (auto-resolved at
 * apply time), and every public hostname currently in scope. The
 * binding-mode hint mirrors the design doc §6 mapping
 * (k8s_ingress vs compose_host_port vs ...).
 */
export function ExposureCard() {
  const q = useRoutingV2();

  if (q.isLoading) {
    return (
      <Card data-testid="exposure-card-loading">
        <CardHeader>
          <CardTitle>Internet exposure</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-20 w-full rounded-md" />
        </CardContent>
      </Card>
    );
  }

  if (q.error || !q.data || !q.data.config || !q.data.config.exposure) {
    return (
      <Card data-testid="exposure-card-error" role="alert">
        <CardHeader>
          <CardTitle>Internet exposure</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-danger">
            Couldn't load v2 routing config:{" "}
            {q.error
              ? (q.error as Error).message
              : !q.data
                ? "no data"
                : !q.data.config
                  ? "missing config field"
                  : "missing exposure field"}
          </p>
        </CardContent>
      </Card>
    );
  }

  const exposure: RoutingV2Exposure = q.data.config.exposure;
  const enabled = Boolean(exposure.enabled);
  const bindingLabel = bindingModeLabel(exposure.binding);
  const publicHostnames = Array.isArray(publicHostnames)
    ? publicHostnames
    : [];

  return (
    <Card data-testid="exposure-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {enabled ? (
            <Globe className="size-4 text-success" aria-hidden />
          ) : (
            <Lock className="size-4 text-fg-muted" aria-hidden />
          )}
          Internet exposure
        </CardTitle>
        <CardDescription>
          Whether public traffic can reach this stack, and how the
          controller binds it.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-fg-muted">Status:</span>
          <Badge
            variant={enabled ? "success" : "outline"}
            data-tone={enabled ? "success" : "muted"}
            data-testid="exposure-status-badge"
          >
            {enabled ? "Exposed" : "Internal only"}
          </Badge>
          <span className="text-fg-muted">·</span>
          <span className="text-fg-muted">Binding:</span>
          <Badge variant="outline" data-testid="exposure-binding-badge">
            {bindingLabel}
          </Badge>
        </div>

        <div className="flex flex-col gap-1">
          <div className="text-xs uppercase tracking-wide text-fg-faint">
            Public hostnames ({publicHostnames.length})
          </div>
          {publicHostnames.length === 0 ? (
            <span className="text-sm text-fg-muted">
              None — no inbound DNS will resolve this controller.
            </span>
          ) : (
            <ul className="flex flex-wrap gap-1.5">
              {publicHostnames.map((h) => (
                <li key={h}>
                  <code className="rounded bg-bg-2 px-1.5 py-0.5 text-xs text-fg">
                    {h}
                  </code>
                </li>
              ))}
            </ul>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function bindingModeLabel(mode: RoutingV2Exposure["binding"]): string {
  switch (mode) {
    case "auto":
      return "auto (resolves at apply)";
    case "k8s_ingress":
      return "K8s Ingress";
    case "k8s_loadbalancer":
      return "K8s LoadBalancer";
    case "compose_host_port":
      return "Compose host port";
    case "compose_loopback":
      return "Compose loopback";
    default:
      return mode;
  }
}
