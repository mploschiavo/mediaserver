import { useEffect, useState } from "react";
import { AlertTriangle, Save } from "lucide-react";
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
  useRoutingV2,
  useRoutingV2Mutation,
  type RoutingV2Apex,
  type RoutingV2CatchAll,
} from "./hooks";

/**
 * Card 4 — Apex + catch-all editor. Operators choose what happens at
 * the bare hostname and at unmatched URLs. Saves via POST
 * /api/routing/v2.
 */
export function ApexCatchAllCard() {
  const q = useRoutingV2();
  const mutation = useRoutingV2Mutation();
  const [editing, setEditing] = useState(false);
  const [apex, setApex] = useState<RoutingV2Apex>({ action: "none" });
  const [catchAll, setCatchAll] = useState<RoutingV2CatchAll>({
    action: "404",
  });

  useEffect(() => {
    if (q.data) {
      setApex(q.data.config.apex);
      setCatchAll(q.data.config.catch_all);
    }
  }, [q.data]);

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

  const gw = q.data.config.gateway_host;

  const handleSave = () => {
    mutation.mutate(
      { apex, catch_all: catchAll },
      { onSuccess: () => setEditing(false) },
    );
  };

  const handleCancel = () => {
    setApex(q.data?.config.apex ?? { action: "none" });
    setCatchAll(q.data?.config.catch_all ?? { action: "404" });
    setEditing(false);
  };

  return (
    <Card data-testid="apex-catchall-card">
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <CardTitle>Apex + catch-all</CardTitle>
          <CardDescription>
            What happens at the bare hostname ({gw || "unset"}) and at
            paths that don't match any route.
          </CardDescription>
        </div>
        {editing ? (
          <div className="flex gap-2">
            <Button variant="ghost" size="sm" onClick={handleCancel}>
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={handleSave}
              disabled={mutation.isPending}
              data-testid="apex-catchall-save"
            >
              <Save className="size-3.5" /> Save
            </Button>
          </div>
        ) : (
          <Button
            size="sm"
            variant="outline"
            onClick={() => setEditing(true)}
            data-testid="apex-catchall-edit"
          >
            Edit
          </Button>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <ApexBlock
          apex={apex}
          gateway={gw}
          editing={editing}
          onChange={setApex}
        />
        <CatchAllBlock
          catch_all={catchAll}
          editing={editing}
          onChange={setCatchAll}
        />
        {mutation.error ? (
          <div
            role="alert"
            className="flex items-start gap-2 rounded-md border border-danger/40 bg-danger/10 p-2 text-xs text-danger"
            data-testid="apex-catchall-error"
          >
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
            <span>{(mutation.error as Error).message}</span>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function ApexBlock({
  apex,
  gateway,
  editing,
  onChange,
}: {
  apex: RoutingV2Apex;
  gateway: string;
  editing: boolean;
  onChange: (next: RoutingV2Apex) => void;
}) {
  if (!editing) {
    return (
      <div className="flex flex-col gap-1" data-testid="apex-row">
        <div className="text-xs uppercase tracking-wide text-fg-faint">
          Apex ({gateway || "unset"})
        </div>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-fg-muted">When the bare host is hit:</span>
          <Badge variant="outline" data-testid="apex-action-badge">
            {apexLabel(apex.action)}
          </Badge>
          {apex.target ? (
            <code className="rounded bg-bg-2 px-1.5 py-0.5 text-xs text-fg">
              {apex.target}
            </code>
          ) : null}
          {apex.code && apex.action === "redirect" ? (
            <Badge variant="outline" className="tabular-nums text-xs">
              {apex.code}
            </Badge>
          ) : null}
        </div>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2" data-testid="apex-row-edit">
      <div className="text-xs uppercase tracking-wide text-fg-faint">
        Apex ({gateway || "unset"})
      </div>
      <select
        value={apex.action}
        onChange={(e) =>
          onChange({
            ...apex,
            action: e.target.value as RoutingV2Apex["action"],
          })
        }
        className="w-full rounded-md border border-border bg-bg-1 px-2 py-1 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-ring"
        data-testid="apex-action-select"
      >
        <option value="none">Fall through (no apex rule)</option>
        <option value="redirect">Redirect to path</option>
        <option value="static">Static page</option>
        <option value="service">Forward to service</option>
      </select>
      {apex.action !== "none" ? (
        <input
          type="text"
          value={apex.target ?? ""}
          onChange={(e) => onChange({ ...apex, target: e.target.value })}
          placeholder={
            apex.action === "redirect"
              ? "/apps"
              : apex.action === "service"
                ? "homepage"
                : "<html>...</html>"
          }
          className="w-full rounded-md border border-border bg-bg-1 px-2 py-1 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-ring"
          data-testid="apex-target-input"
        />
      ) : null}
      {apex.action === "redirect" ? (
        <select
          value={apex.code ?? 302}
          onChange={(e) =>
            onChange({ ...apex, code: parseInt(e.target.value, 10) })
          }
          className="w-full rounded-md border border-border bg-bg-1 px-2 py-1 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-ring"
          data-testid="apex-code-select"
        >
          <option value={302}>302 (Found)</option>
          <option value={301}>301 (Moved Permanently)</option>
          <option value={307}>307 (Temporary)</option>
          <option value={308}>308 (Permanent)</option>
        </select>
      ) : null}
    </div>
  );
}

function CatchAllBlock({
  catch_all,
  editing,
  onChange,
}: {
  catch_all: RoutingV2CatchAll;
  editing: boolean;
  onChange: (next: RoutingV2CatchAll) => void;
}) {
  if (!editing) {
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
  return (
    <div className="flex flex-col gap-2" data-testid="catch-all-row-edit">
      <div className="text-xs uppercase tracking-wide text-fg-faint">
        Catch-all (unknown URLs)
      </div>
      <select
        value={catch_all.action}
        onChange={(e) =>
          onChange({
            ...catch_all,
            action: e.target.value as RoutingV2CatchAll["action"],
          })
        }
        className="w-full rounded-md border border-border bg-bg-1 px-2 py-1 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-ring"
        data-testid="catch-all-action-select"
      >
        <option value="404">Plain 404</option>
        <option value="redirect">Redirect</option>
        <option value="block">Block (TCP RST)</option>
        <option value="service">Forward to service</option>
      </select>
      {catch_all.action === "redirect" || catch_all.action === "service" ? (
        <input
          type="text"
          value={catch_all.target ?? ""}
          onChange={(e) =>
            onChange({ ...catch_all, target: e.target.value })
          }
          placeholder={
            catch_all.action === "redirect" ? "/apps" : "homepage"
          }
          className="w-full rounded-md border border-border bg-bg-1 px-2 py-1 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-ring"
          data-testid="catch-all-target-input"
        />
      ) : null}
    </div>
  );
}

function apexLabel(a: RoutingV2Apex["action"]): string {
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
