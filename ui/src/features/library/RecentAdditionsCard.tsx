import { Library } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/layout/EmptyState";
import { flattenRecent, useRecentLibraryAdditions } from "./hooks";

function formatRelative(ts: string | undefined): string {
  if (!ts) return "";
  const t = Date.parse(ts);
  if (!Number.isFinite(t)) return "";
  const delta = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (delta < 60) return "just now";
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

export function RecentAdditionsCard({ limit = 6 }: { limit?: number }) {
  const query = useRecentLibraryAdditions();
  const items = flattenRecent(query.data, limit);

  return (
    <Card data-testid="recent-additions">
      <CardHeader>
        <CardTitle>Recent additions</CardTitle>
        <CardDescription>added in the last 24 hours</CardDescription>
      </CardHeader>
      <CardContent>
        {query.isLoading ? (
          <div
            className="flex flex-col gap-2"
            data-testid="recent-additions-loading"
          >
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : query.error ? (
          <div
            role="alert"
            data-testid="recent-additions-error"
            className="text-sm text-danger"
          >
            {query.error.message}
          </div>
        ) : items.length === 0 ? (
          <EmptyState
            icon={Library}
            title="Your library is quiet"
            description="*arr will grab something soon."
          />
        ) : (
          <ul
            className="flex flex-col divide-y divide-border"
            role="list"
            data-testid="recent-additions-list"
          >
            {items.map((item) => (
              <li
                key={item.id}
                className="flex items-center gap-3 py-2 text-sm"
              >
                {item.poster ? (
                  <img
                    src={item.poster}
                    alt=""
                    aria-hidden
                    loading="lazy"
                    className="size-10 shrink-0 rounded-sm bg-bg-2 object-cover"
                  />
                ) : (
                  <div
                    aria-hidden
                    className="flex size-10 shrink-0 items-center justify-center rounded-sm bg-bg-2 text-fg-faint"
                  >
                    <Library className="size-4" />
                  </div>
                )}
                <div className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate font-medium text-fg">
                    {item.title}
                  </span>
                  <span className="font-mono text-xs uppercase text-fg-muted">
                    {item.service ?? ""}
                  </span>
                </div>
                <span className="shrink-0 font-mono text-xs tabular-nums text-fg-muted">
                  {formatRelative(item.added)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
