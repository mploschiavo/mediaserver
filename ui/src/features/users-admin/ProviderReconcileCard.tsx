import { asArray } from "@/lib/coerce";
import { Link2, Link2Off, Network, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { EmptyState } from "@/components/layout/EmptyState";
import {
  useImportOrphanUser,
  useUnlinkGhostUser,
  useUserProviders,
  useUsersReconcile,
  usersAdminKeys,
  type UserProvider,
  type ReconcileDiff,
} from "./hooks";

const PROVIDER_NAMES = ["authelia", "jellyfin", "jellyseerr"] as const;

function explain(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}

function readProvider(
  row: UserProvider,
  provider: string,
): { externalId?: string; status?: string } | undefined {
  const p = row.providers?.[provider];
  if (!p) return undefined;
  if (typeof p === "string") return { externalId: p };
  // Wire payload uses snake_case (`external_id`); normalize to the
  // camelCase shape the badge / unlink button render against.
  return { externalId: p.external_id, status: p.status };
}

function userIdOf(row: UserProvider): string {
  return String(row.user_id ?? row.username ?? "");
}

export function ProviderReconcileCard() {
  const providers = useUserProviders();
  const reconcile = useUsersReconcile();
  const importOrphan = useImportOrphanUser();
  const unlink = useUnlinkGhostUser();
  const qc = useQueryClient();

  const list = asArray(providers.data?.providers);
  const diffs = asArray(reconcile.data?.diffs);

  const handleReconcile = () => {
    void qc.invalidateQueries({ queryKey: usersAdminKeys.reconcile });
    void qc.invalidateQueries({ queryKey: usersAdminKeys.providers });
    toast.success("Reconcile triggered");
  };

  const handleLinkOrphan = (diff: ReconcileDiff) => {
    if (!diff.provider_name || !diff.external_id) {
      toast.error("Cannot link — diff is missing provider/external id");
      return;
    }
    importOrphan.mutate(
      {
        provider_name: diff.provider_name,
        external_id: diff.external_id,
      },
      {
        onSuccess: () => toast.success("Imported orphan provider user"),
        onError: (err) =>
          toast.error(`Import failed: ${explain(err, "request failed")}`),
      },
    );
  };

  const handleUnlinkGhost = (
    user_id: string,
    provider_name: string,
  ) => {
    unlink.mutate(
      { user_id, provider_name },
      {
        onSuccess: () => toast.success(`Unlinked ${provider_name}`),
        onError: (err) =>
          toast.error(`Unlink failed: ${explain(err, "request failed")}`),
      },
    );
  };

  return (
    <Card data-testid="provider-reconcile-card">
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
        <div className="flex flex-col gap-1.5">
          <CardTitle className="flex items-center gap-2">
            <Network aria-hidden className="size-4 text-fg-muted" />
            Provider reconciliation
          </CardTitle>
          <CardDescription>
            User-provider bindings across Authelia, Jellyfin, Jellyseerr.
          </CardDescription>
        </div>
        <Button
          variant="secondary"
          size="sm"
          onClick={handleReconcile}
          data-testid="provider-reconcile-run"
        >
          <RotateCcw aria-hidden /> Run reconcile
        </Button>
      </CardHeader>

      <CardContent className="flex flex-col gap-4 p-0">
        {providers.isLoading ? (
          <div
            className="space-y-2 p-6"
            data-testid="provider-reconcile-loading"
          >
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : providers.error ? (
          <p
            role="alert"
            className="px-6 py-4 text-sm text-danger"
            data-testid="provider-reconcile-error"
          >
            {providers.error.message}
          </p>
        ) : list.length === 0 ? (
          <div className="p-6">
            <EmptyState
              icon={Network}
              title="No provider bindings"
              description="No users have been mapped to any provider yet."
            />
          </div>
        ) : (
          <Table data-testid="provider-reconcile-table">
            <TableHeader>
              <TableRow>
                <TableHead>User</TableHead>
                {PROVIDER_NAMES.map((p) => (
                  <TableHead key={p} className="capitalize">
                    {p}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {list.map((row) => {
                const userId = userIdOf(row);
                return (
                  <TableRow
                    key={userId || row.username}
                    data-testid={`provider-row-${userId || row.username}`}
                  >
                    <TableCell className="font-medium text-fg">
                      {row.username ?? userId}
                    </TableCell>
                    {PROVIDER_NAMES.map((p) => {
                      const cell = readProvider(row, p);
                      return (
                        <TableCell key={p}>
                          {cell ? (
                            <div className="flex items-center gap-2">
                              <Badge variant="success">
                                <Link2 aria-hidden className="size-3" />
                                {cell.externalId ?? "linked"}
                              </Badge>
                              <Button
                                size="sm"
                                variant="ghost"
                                disabled={unlink.isPending || !userId}
                                onClick={() => handleUnlinkGhost(userId, p)}
                                data-testid={`provider-unlink-${userId}-${p}`}
                                aria-label={`Unlink ${p} from ${row.username}`}
                              >
                                <Link2Off aria-hidden /> Unlink
                              </Button>
                            </div>
                          ) : (
                            <span className="text-fg-faint">—</span>
                          )}
                        </TableCell>
                      );
                    })}
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}

        {diffs.length > 0 ? (
          <div
            className="border-t border-border px-6 py-4"
            data-testid="reconcile-diffs"
          >
            <h4 className="text-sm font-medium text-fg">
              Pending diffs ({diffs.length})
            </h4>
            <ul className="mt-2 flex flex-col gap-2">
              {diffs.map((diff, idx) => (
                <li
                  key={`${diff.user_id ?? idx}-${diff.provider_name ?? idx}`}
                  className="flex items-center justify-between gap-2 rounded-md border border-border bg-bg-1 px-3 py-2 text-sm"
                  data-testid={`reconcile-diff-${idx}`}
                >
                  <span className="flex flex-wrap items-center gap-2">
                    <Badge
                      variant={diff.kind === "ghost" ? "danger" : "warning"}
                    >
                      {diff.kind ?? "diff"}
                    </Badge>
                    <span className="text-fg">
                      {diff.username ?? diff.user_id ?? "(unknown)"}
                    </span>
                    {diff.provider_name ? (
                      <span className="text-fg-muted">
                        @ {diff.provider_name}
                      </span>
                    ) : null}
                  </span>
                  {diff.kind === "orphan" ? (
                    <Button
                      size="sm"
                      variant="primary"
                      disabled={importOrphan.isPending}
                      onClick={() => handleLinkOrphan(diff)}
                      data-testid={`reconcile-link-${idx}`}
                    >
                      <Link2 aria-hidden /> Link
                    </Button>
                  ) : diff.kind === "ghost" && diff.user_id && diff.provider_name ? (
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={unlink.isPending}
                      onClick={() =>
                        handleUnlinkGhost(diff.user_id!, diff.provider_name!)
                      }
                      data-testid={`reconcile-unlink-${idx}`}
                    >
                      <Link2Off aria-hidden /> Unlink
                    </Button>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
