import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Plus, Radio, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api";
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
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/layout/EmptyState";
import {
  ResponsiveTable,
  type ResponsiveTableColumn,
} from "@/components/layout/ResponsiveTable";
import {
  useLivetvSources,
  useSaveLivetvSources,
  type LivetvUrlEntry,
} from "./hooks";

function explain(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}

function truncate(value: string | undefined, max = 56): string {
  if (!value) return "";
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}

type Kind = "tuner" | "guide";

interface SourceFormState {
  kind: Kind;
  name: string;
  url: string;
}

const EMPTY_FORM: SourceFormState = {
  kind: "tuner",
  name: "",
  url: "",
};

/**
 * Live-TV / IPTV configuration — `tuners[]` (M3U playlists) and
 * `guides[]` (XMLTV EPG) live in two parallel arrays under
 * `/api/livetv-sources`. The currently-selected pair is the scalar
 * (`tuner_url`, `guide_url`); we render both lists and highlight the
 * active row in each.
 */
export function LivetvSourcesCard() {
  const sources = useLivetvSources();
  const tuners = asArray<LivetvUrlEntry>(sources.data?.tuners);
  const guides = asArray<LivetvUrlEntry>(sources.data?.guides);
  const tunerUrl = sources.data?.tuner_url ?? "";
  const guideUrl = sources.data?.guide_url ?? "";

  return (
    <Card data-testid="livetv-sources-card">
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
        <div className="flex flex-col gap-1.5">
          <CardTitle className="flex items-center gap-2">
            <Radio aria-hidden className="size-4 text-fg-muted" />
            Live-TV sources
          </CardTitle>
          <CardDescription>
            M3U playlists (tuners) and EPG XMLTV URLs (guides) Jellyfin
            pulls for live TV. The active pair is highlighted.
          </CardDescription>
        </div>
        <SourceDialog
          tuners={tuners}
          guides={guides}
          trigger={
            <Button
              variant="primary"
              size="sm"
              data-testid="livetv-add-trigger"
            >
              <Plus aria-hidden /> Add source
            </Button>
          }
        />
      </CardHeader>
      <CardContent className="flex flex-col gap-6 p-0">
        {sources.isLoading ? (
          <div className="space-y-2 p-6" data-testid="livetv-sources-loading">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : sources.error ? (
          <p
            role="alert"
            className="px-6 py-4 text-sm text-danger"
            data-testid="livetv-sources-error"
          >
            {sources.error.message}
          </p>
        ) : tuners.length === 0 && guides.length === 0 ? (
          <div className="p-6">
            <EmptyState
              icon={Radio}
              title="No live-TV sources"
              description="Add an M3U tuner playlist plus an optional EPG XMLTV guide URL to start streaming."
            />
          </div>
        ) : (
          <>
            <SourceList
              kind="tuner"
              entries={tuners}
              activeUrl={tunerUrl}
              tuners={tuners}
              guides={guides}
            />
            <SourceList
              kind="guide"
              entries={guides}
              activeUrl={guideUrl}
              tuners={tuners}
              guides={guides}
            />
          </>
        )}
      </CardContent>
    </Card>
  );
}

function SourceList({
  kind,
  entries,
  activeUrl,
  tuners,
  guides,
}: {
  kind: Kind;
  entries: readonly LivetvUrlEntry[];
  activeUrl: string;
  tuners: readonly LivetvUrlEntry[];
  guides: readonly LivetvUrlEntry[];
}) {
  const save = useSaveLivetvSources();
  const heading = kind === "tuner" ? "Tuners (M3U)" : "Guides (XMLTV EPG)";

  const handleDelete = (row: LivetvUrlEntry) => {
    if (
      typeof window !== "undefined" &&
      !window.confirm(`Delete ${kind} source ${row.name}?`)
    ) {
      return;
    }
    if (kind === "tuner") {
      const next = tuners.filter((t) => t.url !== row.url);
      save.mutate(
        { tuners: next },
        {
          onSuccess: () => toast.success(`Deleted ${row.name}`),
          onError: (err) =>
            toast.error(`Delete failed: ${explain(err, "request failed")}`),
        },
      );
    } else {
      const next = guides.filter((g) => g.url !== row.url);
      save.mutate(
        { guides: next },
        {
          onSuccess: () => toast.success(`Deleted ${row.name}`),
          onError: (err) =>
            toast.error(`Delete failed: ${explain(err, "request failed")}`),
        },
      );
    }
  };

  const handleActivate = (row: LivetvUrlEntry) => {
    save.mutate(
      kind === "tuner" ? { tuner_url: row.url } : { guide_url: row.url },
      {
        onSuccess: () => toast.success(`${row.name} is now active`),
        onError: (err) =>
          toast.error(`Activate failed: ${explain(err, "request failed")}`),
      },
    );
  };

  const columns: ResponsiveTableColumn<LivetvUrlEntry>[] = [
    {
      id: "name",
      header: "Name",
      cell: (row) => (
        <div className="flex items-center gap-2">
          <span className="font-medium text-fg">{row.name}</span>
          {row.url === activeUrl ? (
            <Badge variant="success" data-testid={`livetv-${kind}-active`}>
              active
            </Badge>
          ) : null}
        </div>
      ),
    },
    {
      id: "url",
      header: "URL",
      cell: (row) => (
        <span
          className="font-mono text-xs text-fg-muted"
          title={row.url ?? ""}
        >
          {truncate(row.url) || "—"}
        </span>
      ),
    },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      cell: (row) => (
        <div className="flex items-center justify-end gap-1">
          {row.url !== activeUrl ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => handleActivate(row)}
              data-testid={`livetv-${kind}-activate-${row.name}`}
            >
              Use
            </Button>
          ) : null}
          <Button
            variant="ghost"
            size="icon"
            onClick={() => handleDelete(row)}
            aria-label={`Delete ${row.name}`}
            data-testid={`livetv-${kind}-delete-${row.name}`}
          >
            <Trash2 aria-hidden className="text-danger" />
          </Button>
        </div>
      ),
    },
  ];

  return (
    <div className="flex flex-col gap-2 px-6 pb-4">
      <h3 className="text-sm font-medium text-fg" data-testid={`livetv-${kind}-heading`}>
        {heading}{" "}
        <span className="text-fg-muted font-normal">({entries.length})</span>
      </h3>
      {entries.length === 0 ? (
        <p className="rounded-md border border-dashed border-border p-4 text-sm text-fg-muted">
          No {kind === "tuner" ? "tuners" : "guides"} configured.
        </p>
      ) : (
        <ResponsiveTable
          rows={[...entries]}
          rowKey={(r) => r.url}
          columns={columns}
          card={(row) => (
            <div
              className="flex flex-col gap-2"
              data-testid={`livetv-${kind}-card-${row.name}`}
            >
              <div className="flex items-center justify-between">
                <span className="font-medium text-fg">{row.name}</span>
                {row.url === activeUrl ? (
                  <Badge variant="success">active</Badge>
                ) : (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleActivate(row)}
                  >
                    Use
                  </Button>
                )}
              </div>
              <span className="truncate font-mono text-xs text-fg-muted">
                {row.url}
              </span>
              <div className="flex justify-end">
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => handleDelete(row)}
                  aria-label={`Delete ${row.name}`}
                >
                  <Trash2 aria-hidden className="text-danger" />
                </Button>
              </div>
            </div>
          )}
        />
      )}
    </div>
  );
}

interface SourceDialogProps {
  tuners: readonly LivetvUrlEntry[];
  guides: readonly LivetvUrlEntry[];
  trigger: ReactNode;
}

function SourceDialog({ tuners, guides, trigger }: SourceDialogProps) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<SourceFormState>(EMPTY_FORM);
  const save = useSaveLivetvSources();

  // Reset form whenever the dialog opens so a previous draft doesn't
  // bleed into the next session.
  useEffect(() => {
    if (open) setForm(EMPTY_FORM);
  }, [open]);

  const update1 = (patch: Partial<SourceFormState>) =>
    setForm((prev) => ({ ...prev, ...patch }));

  const handleSubmit = (ev: FormEvent) => {
    ev.preventDefault();
    const name = form.name.trim();
    const url = form.url.trim();
    if (!name) {
      toast.error("Source name required");
      return;
    }
    if (!url) {
      toast.error("URL required");
      return;
    }

    const existing = form.kind === "tuner" ? tuners : guides;
    if (existing.some((s) => s.url === url)) {
      toast.error(`URL already added`);
      return;
    }

    const next: LivetvUrlEntry = { url, name };

    save.mutate(
      form.kind === "tuner"
        ? { tuners: [...tuners, next] }
        : { guides: [...guides, next] },
      {
        onSuccess: () => {
          toast.success(`Added ${name}`);
          setOpen(false);
        },
        onError: (err) =>
          toast.error(`Save failed: ${explain(err, "request failed")}`),
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent data-testid="livetv-add-dialog">
        <DialogHeader>
          <DialogTitle>Add live-TV source</DialogTitle>
          <DialogDescription>
            Pick whether you&apos;re adding an M3U tuner playlist or an
            XMLTV EPG guide URL, then provide the URL.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={handleSubmit}
          aria-label="Add live-TV source"
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="livetv-kind">Kind</Label>
            <Select
              value={form.kind}
              onValueChange={(v) => update1({ kind: v as Kind })}
            >
              <SelectTrigger id="livetv-kind" data-testid="livetv-kind">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="tuner">Tuner (M3U playlist)</SelectItem>
                <SelectItem value="guide">Guide (XMLTV EPG)</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="livetv-name">Source name</Label>
            <Input
              id="livetv-name"
              value={form.name}
              onChange={(e) => update1({ name: e.target.value })}
              placeholder={form.kind === "tuner" ? "My IPTV pack" : "My EPG guide"}
              required
              data-testid="livetv-name"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="livetv-url">
              {form.kind === "tuner" ? "M3U playlist URL" : "EPG XMLTV URL"}
            </Label>
            <Input
              id="livetv-url"
              type="url"
              value={form.url}
              onChange={(e) => update1({ url: e.target.value })}
              placeholder={
                form.kind === "tuner"
                  ? "https://example.com/playlist.m3u"
                  : "https://example.com/epg.xml"
              }
              required
              data-testid="livetv-url"
            />
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="secondary">
                Cancel
              </Button>
            </DialogClose>
            <Button
              type="submit"
              variant="primary"
              loading={save.isPending}
              data-testid="livetv-submit"
            >
              Add
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
