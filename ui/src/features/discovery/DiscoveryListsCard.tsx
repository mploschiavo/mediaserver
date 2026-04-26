import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Telescope, Tv } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/layout/EmptyState";
import { fetcher } from "@/api/client";
import { toast } from "sonner";
import { useDiscoveryLists, usePopularTv } from "./hooks";

function explain(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}

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
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <CardTitle>Discovery</CardTitle>
          <CardDescription>
            Curated feeds + popular TV picks. Add a source below; the
            controller queues a bootstrap to write the list into the
            relevant Sonarr/Radarr import-list config.
          </CardDescription>
        </div>
        <AddDiscoverySourceDialog />
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

interface DiscoverySource {
  name: string;
  source: string;
  url?: string;
  list_id?: string;
  target?: "sonarr" | "radarr";
}

const SOURCE_TYPES = [
  { value: "trakt_list", label: "Trakt list / watchlist" },
  { value: "imdb_list", label: "IMDb list" },
  { value: "rss", label: "RSS feed" },
  { value: "plex_playlist", label: "Plex playlist" },
] as const;

/**
 * "Add discovery source" modal. Three-field form:
 *   * name — display label
 *   * source — picker (Trakt / IMDb / RSS / Plex)
 *   * url or list_id — depends on source kind
 *   * target — Sonarr (TV) vs Radarr (movies)
 *
 * Save POSTs the appended array to /api/discovery-lists; the
 * controller queues a bootstrap that writes the new list into
 * the corresponding arr-app's import-list config. The "you must
 * run bootstrap" note is shown inline because the change isn't
 * live until that finishes.
 */
function AddDiscoverySourceDialog() {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<DiscoverySource>({
    name: "",
    source: "trakt_list",
    target: "sonarr",
  });
  const qc = useQueryClient();
  const lists = useDiscoveryLists();

  const mut = useMutation({
    mutationFn: async (next: DiscoverySource[]) =>
      fetcher("api/discovery-lists", {
        method: "POST",
        body: JSON.stringify({ lists: next }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["discovery-lists"] });
      toast.success(
        "Source saved — bootstrap queued to apply.",
      );
      setOpen(false);
      setDraft({ name: "", source: "trakt_list", target: "sonarr" });
    },
    onError: (err) =>
      toast.error(`Save failed: ${explain(err, "request failed")}`),
  });

  const handleSave = () => {
    const existing = Array.isArray(lists.data?.lists)
      ? (lists.data!.lists as unknown[]).filter(
          (e): e is DiscoverySource =>
            !!e && typeof e === "object" && "name" in e,
        )
      : [];
    const cleaned: DiscoverySource = {
      name: draft.name.trim(),
      source: draft.source,
      url: draft.url?.trim() || undefined,
      list_id: draft.list_id?.trim() || undefined,
      target: draft.target,
    };
    if (!cleaned.name || (!cleaned.url && !cleaned.list_id)) {
      toast.error(
        "Name + (URL or list ID) are required.",
      );
      return;
    }
    mut.mutate([...existing, cleaned]);
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          size="sm"
          variant="outline"
          data-testid="discovery-add-button"
        >
          <Plus className="size-3.5" /> Add source
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add discovery source</DialogTitle>
          <DialogDescription>
            Curated feeds populate Sonarr / Radarr import lists. The
            controller writes the entry to the profile YAML and
            queues a bootstrap; the new list is live after bootstrap
            completes (usually 30–60s).
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            handleSave();
          }}
        >
          <div className="flex flex-col gap-1">
            <Label htmlFor="ds-name">Name</Label>
            <Input
              id="ds-name"
              value={draft.name}
              onChange={(e) =>
                setDraft({ ...draft, name: e.target.value })
              }
              placeholder="e.g. Trakt: My watchlist"
              data-testid="discovery-add-name"
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="ds-source">Source type</Label>
            <select
              id="ds-source"
              value={draft.source}
              onChange={(e) =>
                setDraft({ ...draft, source: e.target.value })
              }
              className="rounded-md border border-border bg-bg-1 px-2 py-1 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-ring"
              data-testid="discovery-add-source-type"
            >
              {SOURCE_TYPES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>
          {draft.source === "rss" ? (
            <div className="flex flex-col gap-1">
              <Label htmlFor="ds-url">RSS URL</Label>
              <Input
                id="ds-url"
                type="url"
                value={draft.url ?? ""}
                onChange={(e) =>
                  setDraft({ ...draft, url: e.target.value })
                }
                placeholder="https://…/feed.xml"
                data-testid="discovery-add-url"
              />
            </div>
          ) : (
            <div className="flex flex-col gap-1">
              <Label htmlFor="ds-list-id">List ID</Label>
              <Input
                id="ds-list-id"
                value={draft.list_id ?? ""}
                onChange={(e) =>
                  setDraft({ ...draft, list_id: e.target.value })
                }
                placeholder={
                  draft.source === "trakt_list"
                    ? "username/list-name OR watchlist:username"
                    : draft.source === "imdb_list"
                      ? "ls012345678"
                      : "playlist-id"
                }
                data-testid="discovery-add-list-id"
              />
            </div>
          )}
          <div className="flex flex-col gap-1">
            <Label htmlFor="ds-target">Target</Label>
            <select
              id="ds-target"
              value={draft.target ?? "sonarr"}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  target: e.target.value as "sonarr" | "radarr",
                })
              }
              className="rounded-md border border-border bg-bg-1 px-2 py-1 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-ring"
              data-testid="discovery-add-target"
            >
              <option value="sonarr">Sonarr (TV)</option>
              <option value="radarr">Radarr (movies)</option>
            </select>
          </div>
        </form>
        <DialogFooter>
          <Button
            variant="ghost"
            onClick={() => setOpen(false)}
            data-testid="discovery-add-cancel"
          >
            Cancel
          </Button>
          <Button
            onClick={handleSave}
            disabled={mut.isPending}
            data-testid="discovery-add-save"
          >
            {mut.isPending ? "Saving…" : "Add source"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
