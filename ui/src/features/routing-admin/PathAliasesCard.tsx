import { ArrowRight } from "lucide-react";
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
 * Read-only view of `routing.path_aliases` — the HTTP redirects
 * (e.g. /app/jellyfin → /app/jf). Each row shows from / to / status
 * code. Edit lands in PR-5.5 (the full editor flow).
 */
export function PathAliasesCard() {
  const q = useRoutingV2();

  if (q.isLoading) {
    return (
      <Card data-testid="path-aliases-card-loading">
        <CardHeader>
          <CardTitle>Path aliases</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-24 w-full rounded-md" />
        </CardContent>
      </Card>
    );
  }

  if (q.error || !q.data) {
    return (
      <Card data-testid="path-aliases-card-error" role="alert">
        <CardHeader>
          <CardTitle>Path aliases</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-danger">
            Couldn't load path aliases:{" "}
            {q.error ? (q.error as Error).message : "no data"}
          </p>
        </CardContent>
      </Card>
    );
  }

  const aliases = q.data.config.path_aliases ?? [];

  return (
    <Card data-testid="path-aliases-card">
      <CardHeader>
        <CardTitle>Path aliases</CardTitle>
        <CardDescription>
          HTTP redirects on the gateway host, sorted by source path.
          Suffixes are preserved (e.g. /app/jellyfin/movies/123 →
          /app/jf/movies/123).
        </CardDescription>
      </CardHeader>
      <CardContent>
        {aliases.length === 0 ? (
          <div
            className="rounded-md border border-dashed border-border p-4 text-center text-sm text-fg-muted"
            data-testid="path-aliases-empty"
          >
            No path aliases configured.
          </div>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {aliases.map((a, idx) => (
              <li
                key={`${a.from}-${idx}`}
                className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-bg-1/40 p-2 text-sm"
                data-testid={`path-alias-row-${idx}`}
              >
                <code className="rounded bg-bg-2 px-1.5 py-0.5 text-xs text-fg">
                  {a.from}
                </code>
                <ArrowRight className="size-3.5 text-fg-faint" aria-hidden />
                <code className="rounded bg-bg-2 px-1.5 py-0.5 text-xs text-fg">
                  {a.to}
                </code>
                <Badge variant="outline" className="ml-auto tabular-nums text-xs">
                  {a.code}
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
