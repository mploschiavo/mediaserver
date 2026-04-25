import { useMemo, useState, type FormEvent } from "react";
import { Library, Plus } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api";
import { asArray } from "@/lib/coerce";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { EmptyState } from "@/components/layout/EmptyState";
import {
  ResponsiveTable,
  type ResponsiveTableColumn,
} from "@/components/layout/ResponsiveTable";
import {
  useAddLibrary,
  useLibraries,
  type ConfiguredLibraryEntry,
  type LiveLibraryEntry,
} from "./hooks";

interface LibraryRow {
  id: string;
  name: string;
  kind: string;
  paths: readonly string[];
  count: number;
  monitored: boolean;
}

/**
 * Build the operator-facing row set from the API response. Configured
 * entries are the source of truth for "which libraries exist"; live
 * entries (from Jellyfin) are merged in for `count` only when their
 * name matches a configured entry.
 */
function buildRows(
  configured: readonly ConfiguredLibraryEntry[],
  live: readonly LiveLibraryEntry[],
): LibraryRow[] {
  const liveByName = new Map<string, LiveLibraryEntry>();
  for (const l of live) {
    if (typeof l.name === "string") liveByName.set(l.name, l);
  }
  return configured.map((entry, idx) => {
    const name = entry.name || `Library ${idx + 1}`;
    const kind = entry.collection_type || "unknown";
    const paths = asArray<string>(entry.paths).filter(
      (p): p is string => typeof p === "string",
    );
    const liveMatch = liveByName.get(name);
    const count =
      typeof liveMatch?.item_count === "number" &&
      Number.isFinite(liveMatch.item_count)
        ? liveMatch.item_count
        : 0;
    return {
      id: `${name}-${idx}`,
      name,
      kind,
      paths,
      count,
      monitored: true,
    };
  });
}

function kindVariant(
  kind: string,
): "default" | "info" | "success" | "warning" | "outline" {
  const k = kind.toLowerCase();
  if (k.includes("movie")) return "info";
  if (k.includes("tv") || k.includes("show") || k.includes("series"))
    return "success";
  if (k.includes("music") || k.includes("track") || k.includes("audio"))
    return "warning";
  return "outline";
}

function AddLibraryDialog() {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [type, setType] = useState("movies");
  const [path, setPath] = useState("");
  const add = useAddLibrary();

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !path.trim()) {
      toast.error("Name and path are required");
      return;
    }
    add.mutate(
      {
        name: name.trim(),
        collection_type: type.trim(),
        paths: [path.trim()],
      },
      {
        onSuccess: () => {
          toast.success(`Library "${name.trim()}" saved`);
          setOpen(false);
          setName("");
          setPath("");
          setType("movies");
        },
        onError: (err) => {
          const msg =
            err instanceof ApiError
              ? err.message
              : err instanceof Error
                ? err.message
                : "Failed to save library";
          toast.error(msg);
        },
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="primary" size="sm" data-testid="add-library-trigger">
          <Plus aria-hidden />
          Add library
        </Button>
      </DialogTrigger>
      <DialogContent data-testid="add-library-dialog">
        <DialogHeader>
          <DialogTitle>Add library</DialogTitle>
          <DialogDescription>
            Register a new library with the controller. Jellyfin and the
            *arr apps will pick it up on the next config reload.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <Label htmlFor="add-library-name">Name</Label>
            <Input
              id="add-library-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Movies"
              data-testid="add-library-name"
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="add-library-type">Kind</Label>
            <Input
              id="add-library-type"
              value={type}
              onChange={(e) => setType(e.target.value)}
              placeholder="movies"
              data-testid="add-library-type"
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="add-library-path">Path</Label>
            <Input
              id="add-library-path"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              placeholder="/media/movies"
              data-testid="add-library-path"
            />
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => setOpen(false)}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              loading={add.isPending}
              data-testid="add-library-submit"
            >
              Save library
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function LibrariesTable() {
  const query = useLibraries();
  const rows = useMemo<LibraryRow[]>(() => {
    const configured = asArray<ConfiguredLibraryEntry>(
      query.data?.configured,
    );
    const live = asArray<LiveLibraryEntry>(query.data?.live);
    return buildRows(configured, live);
  }, [query.data]);

  if (query.isLoading) {
    return (
      <div className="space-y-2" data-testid="libraries-table-loading">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    );
  }

  if (query.error) {
    return (
      <div
        role="alert"
        data-testid="libraries-table-error"
        className="rounded-lg border border-[color-mix(in_oklab,var(--color-danger)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-danger)_10%,transparent)] p-4 text-sm text-danger"
      >
        <p className="font-medium">Failed to load libraries</p>
        <p className="mt-1 text-fg-muted">{query.error.message}</p>
      </div>
    );
  }

  const empty =
    rows.length === 0 ? (
      <EmptyState
        icon={Library}
        title="No libraries yet"
        description="Add a Jellyfin library to start indexing media."
        action={<AddLibraryDialog />}
      />
    ) : null;

  const columns: ResponsiveTableColumn<LibraryRow>[] = [
    {
      id: "name",
      header: "Library",
      cell: (row) => (
        <div className="flex flex-col">
          <span className="font-medium text-fg">{row.name}</span>
          {row.paths[0] ? (
            <span className="font-mono text-xs text-fg-faint">
              {row.paths[0]}
            </span>
          ) : null}
        </div>
      ),
    },
    {
      id: "kind",
      header: "Kind",
      cell: (row) => <Badge variant={kindVariant(row.kind)}>{row.kind}</Badge>,
    },
    {
      id: "count",
      header: "Items",
      cell: (row) => (
        <span className="font-mono tabular-nums text-fg">
          {row.count.toLocaleString()}
        </span>
      ),
    },
    {
      id: "monitored",
      header: "Monitored",
      cell: (row) => (
        <Switch
          checked={row.monitored}
          disabled
          aria-label={`${row.name} monitored`}
        />
      ),
    },
  ];

  return (
    <div className="flex flex-col gap-3" data-testid="libraries-table">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm text-fg-muted">
          {rows.length} {rows.length === 1 ? "library" : "libraries"}
        </p>
        <AddLibraryDialog />
      </div>
      {empty ?? (
        <Card className="p-0">
          <ResponsiveTable
            rows={rows}
            rowKey={(r) => r.id}
            columns={columns}
            card={(row) => (
              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium text-fg">{row.name}</span>
                  <Badge variant={kindVariant(row.kind)}>{row.kind}</Badge>
                </div>
                {row.paths[0] ? (
                  <p className="font-mono text-xs text-fg-faint">
                    {row.paths[0]}
                  </p>
                ) : null}
                <div className="flex items-center justify-between text-xs text-fg-muted">
                  <span>{row.count.toLocaleString()} items</span>
                  <Switch
                    checked={row.monitored}
                    disabled
                    aria-label={`${row.name} monitored`}
                  />
                </div>
              </div>
            )}
          />
        </Card>
      )}
    </div>
  );
}
