import { Telescope, Tv } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/layout/EmptyState";
import { useDiscoveryLists, usePopularTv } from "./hooks";

function PopularTvSection() {
  const query = usePopularTv();

  if (query.isLoading) {
    return (
      <div className="flex flex-col gap-2" data-testid="popular-tv-loading">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-8 w-full" />
        ))}
      </div>
    );
  }
  if (query.error) {
    return (
      <div
        role="alert"
        data-testid="popular-tv-error"
        className="text-sm text-danger"
      >
        {query.error.message}
      </div>
    );
  }
  const items = Array.isArray(query.data) ? query.data : [];
  if (items.length === 0) {
    return (
      <p className="text-sm text-fg-muted" data-testid="popular-tv-empty">
        No popular TV picks right now.
      </p>
    );
  }
  return (
    <ul
      className="divide-y divide-border"
      role="list"
      data-testid="popular-tv-list"
    >
      {items.slice(0, 10).map((entry, i) => {
        const title =
          typeof entry.title === "string" ? entry.title : `Title ${i + 1}`;
        const tvdb =
          typeof entry.tvdbId === "number" ? entry.tvdbId : undefined;
        return (
          <li
            key={tvdb ?? `idx-${i}`}
            className="flex items-center gap-3 py-2 text-sm"
          >
            <Tv className="size-4 shrink-0 text-fg-muted" aria-hidden />
            <span className="truncate font-medium text-fg">{title}</span>
            {tvdb !== undefined ? (
              <span className="ml-auto font-mono text-xs text-fg-faint">
                tvdb {tvdb}
              </span>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

function ConfiguredListsSection() {
  const query = useDiscoveryLists();

  if (query.isLoading) {
    return (
      <div
        className="flex flex-col gap-2"
        data-testid="discovery-lists-loading"
      >
        {[0, 1].map((i) => (
          <Skeleton key={i} className="h-8 w-full" />
        ))}
      </div>
    );
  }
  if (query.error) {
    return (
      <div
        role="alert"
        data-testid="discovery-lists-error"
        className="text-sm text-danger"
      >
        {query.error.message}
      </div>
    );
  }
  const rawLists = query.data?.lists;
  const lists = Array.isArray(rawLists) ? rawLists : [];
  if (lists.length === 0) {
    return (
      <EmptyState
        icon={Telescope}
        title="No discovery sources configured"
        description="Browse popular TV below or configure curated discovery feeds."
      />
    );
  }
  return (
    <ul
      className="divide-y divide-border"
      role="list"
      data-testid="discovery-lists-list"
    >
      {lists.map((entry, i) => {
        const name =
          typeof entry?.name === "string" ? entry.name : `List ${i + 1}`;
        const source =
          typeof entry?.source === "string"
            ? entry.source
            : typeof entry?.kind === "string"
              ? entry.kind
              : undefined;
        return (
          <li
            key={i}
            className="flex items-center justify-between py-2 text-sm"
          >
            <span className="font-medium text-fg">{name}</span>
            {source ? (
              <span className="font-mono text-xs text-fg-muted">{source}</span>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

export function DiscoveryListsCard() {
  return (
    <Card data-testid="discovery-lists-card">
      <CardHeader>
        <CardTitle>Discovery</CardTitle>
        <CardDescription>
          Curated feeds + popular TV picks. Browse-only — wire up an Import
          List in Sonarr/Radarr to subscribe.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-6">
        <section aria-label="Configured discovery sources">
          <h4 className="mb-2 text-sm font-medium uppercase tracking-wide text-fg-muted">
            Configured sources
          </h4>
          <ConfiguredListsSection />
        </section>
        <section aria-label="Popular TV">
          <h4 className="mb-2 text-sm font-medium uppercase tracking-wide text-fg-muted">
            Popular TV
          </h4>
          <PopularTvSection />
        </section>
      </CardContent>
    </Card>
  );
}
