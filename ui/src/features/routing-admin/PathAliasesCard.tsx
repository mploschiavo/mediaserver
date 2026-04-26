import { useEffect, useState } from "react";
import { ArrowRight, Plus, Trash2, Save, AlertTriangle } from "lucide-react";
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
  type RoutingV2PathAlias,
} from "./hooks";

/**
 * Card 3 — Path aliases CRUD. Operators can add, edit (in-place via
 * the input fields), and delete redirect rows. Save commits via
 * POST /api/routing/v2; suffix preservation is locked by R-4.
 */
export function PathAliasesCard() {
  const q = useRoutingV2();
  const mutation = useRoutingV2Mutation();
  const [draft, setDraft] = useState<RoutingV2PathAlias[]>([]);
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    if (q.data) setDraft(q.data.config.path_aliases ?? []);
  }, [q.data]);

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

  const aliases = editing ? draft : q.data.config.path_aliases ?? [];

  const updateRow = (idx: number, patch: Partial<RoutingV2PathAlias>) => {
    setDraft((d) => d.map((a, i) => (i === idx ? { ...a, ...patch } : a)));
  };
  const removeRow = (idx: number) =>
    setDraft((d) => d.filter((_, i) => i !== idx));
  const addRow = () =>
    setDraft((d) => [...d, { from: "", to: "", code: 301 }]);

  const handleSave = () => {
    // Strip empty rows so a half-typed alias doesn't fail validation.
    const cleaned = draft.filter((a) => a.from.trim() && a.to.trim());
    mutation.mutate(
      { path_aliases: cleaned },
      { onSuccess: () => setEditing(false) },
    );
  };

  const handleCancel = () => {
    setDraft(q.data?.config.path_aliases ?? []);
    setEditing(false);
  };

  return (
    <Card data-testid="path-aliases-card">
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <CardTitle>Path aliases</CardTitle>
          <CardDescription>
            HTTP redirects on the gateway host, sorted by source path.
            Suffixes are preserved (e.g. /app/jellyfin/movies/123 →
            /app/jf/movies/123).
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
              data-testid="path-aliases-save"
            >
              <Save className="size-3.5" /> Save
            </Button>
          </div>
        ) : (
          <Button
            size="sm"
            variant="outline"
            onClick={() => setEditing(true)}
            data-testid="path-aliases-edit"
          >
            Edit
          </Button>
        )}
      </CardHeader>
      <CardContent>
        {aliases.length === 0 && !editing ? (
          <div
            className="rounded-md border border-dashed border-border p-4 text-center text-sm text-fg-muted"
            data-testid="path-aliases-empty"
          >
            No path aliases configured.
          </div>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {aliases.map((a, idx) =>
              editing ? (
                <li
                  key={idx}
                  className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-bg-1/40 p-2 text-sm"
                  data-testid={`path-alias-row-${idx}`}
                >
                  <input
                    type="text"
                    value={a.from}
                    onChange={(e) => updateRow(idx, { from: e.target.value })}
                    placeholder="/app/jellyfin"
                    className="flex-1 min-w-[140px] rounded border border-border bg-bg-1 px-2 py-1 font-mono text-xs text-fg focus:outline-none focus:ring-2 focus:ring-ring"
                    data-testid={`path-alias-from-${idx}`}
                  />
                  <ArrowRight className="size-3.5 text-fg-faint" aria-hidden />
                  <input
                    type="text"
                    value={a.to}
                    onChange={(e) => updateRow(idx, { to: e.target.value })}
                    placeholder="/app/jf"
                    className="flex-1 min-w-[140px] rounded border border-border bg-bg-1 px-2 py-1 font-mono text-xs text-fg focus:outline-none focus:ring-2 focus:ring-ring"
                    data-testid={`path-alias-to-${idx}`}
                  />
                  <select
                    value={a.code}
                    onChange={(e) =>
                      updateRow(idx, { code: parseInt(e.target.value, 10) })
                    }
                    className="rounded border border-border bg-bg-1 px-1 py-1 text-xs text-fg focus:outline-none focus:ring-2 focus:ring-ring"
                    data-testid={`path-alias-code-${idx}`}
                  >
                    <option value={301}>301</option>
                    <option value={302}>302</option>
                    <option value={307}>307</option>
                    <option value={308}>308</option>
                  </select>
                  <button
                    type="button"
                    onClick={() => removeRow(idx)}
                    className="rounded p-1 text-danger hover:bg-danger/10"
                    aria-label={`Remove alias ${a.from}`}
                    data-testid={`path-alias-remove-${idx}`}
                  >
                    <Trash2 className="size-3.5" aria-hidden />
                  </button>
                </li>
              ) : (
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
              ),
            )}
            {editing ? (
              <li>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={addRow}
                  data-testid="path-aliases-add"
                >
                  <Plus className="size-3.5" /> Add alias
                </Button>
              </li>
            ) : null}
          </ul>
        )}
        {mutation.error ? (
          <div
            role="alert"
            className="mt-3 flex items-start gap-2 rounded-md border border-danger/40 bg-danger/10 p-2 text-xs text-danger"
            data-testid="path-aliases-error"
          >
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
            <span>{(mutation.error as Error).message}</span>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
