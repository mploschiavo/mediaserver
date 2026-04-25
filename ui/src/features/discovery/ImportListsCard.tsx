import { ListPlus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api";
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
import { Switch } from "@/components/ui/switch";
import { EmptyState } from "@/components/layout/EmptyState";
import {
  groupImportLists,
  useDeleteImportList,
  useImportLists,
  useToggleImportList,
  type ImportListEntry,
} from "./hooks";

interface RowProps {
  service: string;
  list: ImportListEntry;
}

function ImportListRow({ service, list }: RowProps) {
  const toggle = useToggleImportList();
  const del = useDeleteImportList();

  const id = typeof list.id === "number" ? list.id : undefined;
  const enabled = list.enabled !== false;
  const name =
    typeof list.name === "string" && list.name ? list.name : `List ${id ?? ""}`;
  const listType =
    typeof list.listType === "string" ? list.listType : undefined;

  const onToggle = (next: boolean) => {
    if (id === undefined) {
      toast.error("List id missing");
      return;
    }
    toggle.mutate(
      { service, listId: id, enabled: next },
      {
        onSuccess: () =>
          toast.success(`${name} ${next ? "enabled" : "disabled"}`),
        onError: (err) => {
          const msg =
            err instanceof ApiError
              ? err.message
              : err instanceof Error
                ? err.message
                : "Toggle failed";
          toast.error(msg);
        },
      },
    );
  };

  const onDelete = () => {
    if (id === undefined) {
      toast.error("List id missing");
      return;
    }
    del.mutate(
      { service, listId: id },
      {
        onSuccess: () => toast.success(`${name} removed`),
        onError: (err) => {
          const msg =
            err instanceof ApiError
              ? err.message
              : err instanceof Error
                ? err.message
                : "Delete failed";
          toast.error(msg);
        },
      },
    );
  };

  return (
    <li
      className="flex items-center justify-between gap-3 py-2 text-sm"
      data-testid={`import-list-${service}-${id ?? "unknown"}`}
    >
      <div className="flex flex-col min-w-0 flex-1">
        <span className="truncate font-medium text-fg">{name}</span>
        <div className="flex items-center gap-2 text-xs text-fg-muted">
          {listType ? <Badge variant="outline">{listType}</Badge> : null}
          {id !== undefined ? (
            <span className="font-mono">id {id}</span>
          ) : null}
        </div>
      </div>
      <Switch
        checked={enabled}
        onCheckedChange={onToggle}
        disabled={toggle.isPending || id === undefined}
        aria-label={`${name} enabled`}
        data-testid={`import-list-toggle-${service}-${id ?? "unknown"}`}
      />
      <Button
        variant="ghost"
        size="sm"
        onClick={onDelete}
        loading={del.isPending}
        aria-label={`Delete ${name}`}
        data-testid={`import-list-delete-${service}-${id ?? "unknown"}`}
      >
        <Trash2 aria-hidden />
      </Button>
    </li>
  );
}

export function ImportListsCard() {
  const query = useImportLists();
  const groups = groupImportLists(query.data);

  return (
    <Card data-testid="import-lists-card">
      <CardHeader>
        <CardTitle>Import lists</CardTitle>
        <CardDescription>
          Discovery lists pulled from each *arr. Toggle a list to control
          whether new entries auto-add.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {query.isLoading ? (
          <div
            className="flex flex-col gap-2"
            data-testid="import-lists-loading"
          >
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : query.error ? (
          <div
            role="alert"
            data-testid="import-lists-error"
            className="text-sm text-danger"
          >
            {query.error.message}
          </div>
        ) : groups.length === 0 ? (
          <EmptyState
            icon={ListPlus}
            title="No import lists yet"
            description="Configure import lists in Sonarr/Radarr to auto-add new releases."
          />
        ) : (
          <div className="flex flex-col gap-4" data-testid="import-lists-groups">
            {groups.map((group) => (
              <section
                key={group.service}
                aria-label={`${group.service} import lists`}
              >
                <h4 className="mb-1 text-sm font-medium uppercase tracking-wide text-fg-muted">
                  {group.service}
                </h4>
                <ul
                  className="divide-y divide-border"
                  role="list"
                  data-testid={`import-lists-${group.service}`}
                >
                  {group.lists.map((list, i) => (
                    <ImportListRow
                      key={typeof list.id === "number" ? list.id : `idx-${i}`}
                      service={group.service}
                      list={list}
                    />
                  ))}
                </ul>
              </section>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
