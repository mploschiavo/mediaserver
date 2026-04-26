import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useRoutingV2, type RoutingV2Apex, type RoutingV2CatchAll } from "./hooks";

/**
 * Read-only view of `routing.apex` and `routing.catch_all` — the
 * "what happens at the bare hostname" + "what happens at unknown
 * URLs" config. Card 4 from the design doc; edit is in PR-5.5.
 */
export function ApexCatchAllCard() {
  const q = useRoutingV2();

  if (q.isLoading) {
    return (
      <Card data-testid="apex-catchall-card-loading">
        <CardHeader>
          <CardTitle>Apex + catch-all</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-32 w-full rounded-md" />
        </CardContent>
      </Card>
    );
  }

  if (q.error || !q.data) {
    return (
      <Card data-testid="apex-catchall-card-error" role="alert">
        <CardHeader>
          <CardTitle>Apex + catch-all</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-danger">
            Couldn't load apex/catch-all:{" "}
            {q.error ? (q.error as Error).message : "no data"}
          </p>
        </CardContent>
      </Card>
    );
  }

  const apex = q.data.config.apex;
  const catchAll = q.data.config.catch_all;
  const gw = q.data.config.gateway_host;

  return (
    <Card data-testid="apex-catchall-card">
      <CardHeader>
        <CardTitle>Apex + catch-all</CardTitle>
        <CardDescription>
          What happens at the bare hostname ({gw || "unset"}) and at
          paths that don't match any route. Edit lands in PR-5.5.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <ApexRow apex={apex} gateway={gw} />
        <CatchAllRow catch_all={catchAll} />
      </CardContent>
    </Card>
  );
}

function ApexRow({ apex, gateway }: { apex: RoutingV2Apex; gateway: string }) {
  return (
    <div className="flex flex-col gap-1" data-testid="apex-row">
      <div className="text-xs uppercase tracking-wide text-fg-faint">
        Apex ({gateway || "unset"})
      </div>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-fg-muted">When the bare host is hit:</span>
        <Badge variant="outline" data-testid="apex-action-badge">
          {actionLabel(apex.action)}
        </Badge>
        {apex.target ? (
          <code className="rounded bg-bg-2 px-1.5 py-0.5 text-xs text-fg">
            {apex.target}
          </code>
        ) : null}
        {apex.code ? (
          <Badge variant="outline" className="tabular-nums text-xs">
            {apex.code}
          </Badge>
        ) : null}
      </div>
    </div>
  );
}

function CatchAllRow({ catch_all }: { catch_all: RoutingV2CatchAll }) {
  const tone =
    catch_all.action === "block" || catch_all.action === "404"
      ? "warning"
      : "muted";
  return (
    <div className="flex flex-col gap-1" data-testid="catch-all-row">
      <div className="text-xs uppercase tracking-wide text-fg-faint">
        Catch-all (unknown URLs)
      </div>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-fg-muted">When no route matches:</span>
        <Badge
          variant="outline"
          data-tone={tone}
          data-testid="catch-all-action-badge"
        >
          {catchAllLabel(catch_all.action)}
        </Badge>
        {catch_all.target ? (
          <code className="rounded bg-bg-2 px-1.5 py-0.5 text-xs text-fg">
            {catch_all.target}
          </code>
        ) : null}
        {catch_all.code && catch_all.action === "redirect" ? (
          <Badge variant="outline" className="tabular-nums text-xs">
            {catch_all.code}
          </Badge>
        ) : null}
      </div>
    </div>
  );
}

function actionLabel(a: RoutingV2Apex["action"]): string {
  switch (a) {
    case "none":
      return "Fall through (no apex rule)";
    case "redirect":
      return "Redirect";
    case "static":
      return "Static page";
    case "service":
      return "Forward to service";
  }
}

function catchAllLabel(a: RoutingV2CatchAll["action"]): string {
  switch (a) {
    case "404":
      return "Plain 404";
    case "redirect":
      return "Redirect";
    case "block":
      return "Block (TCP RST)";
    case "service":
      return "Forward to service";
  }
}
