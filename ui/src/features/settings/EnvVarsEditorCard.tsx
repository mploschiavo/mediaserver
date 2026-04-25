import { useEffect, useMemo, useState } from "react";
import { Eye, EyeOff, Plus, Save, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { ApiError, fetcher } from "@/api";
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
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { asArray } from "@/lib/coerce";
import { useQueryClient, useMutation } from "@tanstack/react-query";
import {
  isSensitiveKey,
  settingsKeys,
  useDeleteEnvVar,
  useEnvVars,
  type EnvVarEntry,
  type EnvVarsResponse,
} from "./hooks";

interface EditableRow {
  /** Server-stable id used as the React key. Falls back to "new-N". */
  rowId: string;
  key: string;
  value: string;
  sensitive: boolean;
  isNew: boolean;
}

function fromServer(data: EnvVarsResponse | undefined): EditableRow[] {
  if (!data) return [];
  const list = asArray<EnvVarEntry>(data.vars ?? data.env_vars);
  return list.map((e, idx) => {
    const key = String(e.key ?? e.name ?? "");
    return {
      rowId: key || `row-${idx}`,
      key,
      value: typeof e.value === "string" ? e.value : "",
      sensitive:
        typeof e.sensitive === "boolean" ? e.sensitive : isSensitiveKey(key),
      isNew: false,
    };
  });
}

function errMsg(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}

/**
 * Local-only "reveal" set. Toggling a row flips it in this set;
 * the value is never logged or persisted. The set re-creates on
 * unmount so a remount re-masks every sensitive row.
 */
function useRevealedSet(): {
  revealed: ReadonlySet<string>;
  toggle: (id: string) => void;
} {
  const [set, setSet] = useState<Set<string>>(() => new Set());
  return {
    revealed: set,
    toggle: (id: string) =>
      setSet((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
      }),
  };
}

/**
 * Editable env vars surface. Per-row save (POST /api/envvars), an
 * "Add" button for new keys, and reveal toggles for sensitive
 * values. Sensitive masking happens locally — the value never
 * makes it to console even when revealed.
 */
export function EnvVarsEditorCard() {
  const envVars = useEnvVars();
  const qc = useQueryClient();
  const saveMutation = useMutation({
    mutationFn: (body: EnvVarEntry) =>
      fetcher<EnvVarEntry>("api/envvars", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: settingsKeys.envVars });
    },
  });
  // POST /api/envvars/delete (server lacks DELETE method dispatch).
  // Symmetric with the save above; the server's allow-prefix guard
  // applies the same set of platform/service prefixes so the
  // dashboard can't drop arbitrary host vars. Optimistic update +
  // rollback live in ``useDeleteEnvVar`` so the row snaps out before
  // the network round-trip and slides back if the server rejects.
  const deleteMutation = useDeleteEnvVar();

  const initial = useMemo(() => fromServer(envVars.data), [envVars.data]);
  const [rows, setRows] = useState<EditableRow[]>([]);
  const { revealed, toggle: toggleReveal } = useRevealedSet();
  const [pendingId, setPendingId] = useState<string | null>(null);
  /**
   * Confirmation dialog state. We intentionally use the shared
   * Radix dialog primitive rather than ``window.confirm`` —
   * happy-dom's confirm is a no-op stub, and the modal lets us
   * announce the destructive action via ``role="alertdialog"``.
   */
  const [confirmTarget, setConfirmTarget] = useState<EditableRow | null>(null);

  useEffect(() => {
    setRows(initial);
  }, [initial]);

  const handleAdd = () => {
    const id = `new-${Date.now()}`;
    setRows((prev) => [
      ...prev,
      { rowId: id, key: "", value: "", sensitive: false, isNew: true },
    ]);
  };

  const updateRow = (id: string, patch: Partial<EditableRow>) => {
    setRows((prev) =>
      prev.map((r) => {
        if (r.rowId !== id) return r;
        const next = { ...r, ...patch };
        // Recompute sensitivity if the key changed.
        if (patch.key !== undefined) {
          next.sensitive = isSensitiveKey(next.key);
        }
        return next;
      }),
    );
  };

  const handleSaveRow = (row: EditableRow) => {
    if (!row.key.trim()) {
      toast.error("Key is required");
      return;
    }
    setPendingId(row.rowId);
    saveMutation.mutate(
      { key: row.key, value: row.value },
      {
        onSuccess: () => {
          toast.success(`Saved ${row.key}`);
          setPendingId(null);
        },
        onError: (err) => {
          toast.error(errMsg(err, "Save failed"));
          setPendingId(null);
        },
      },
    );
  };

  const handleDeleteRow = (row: EditableRow) => {
    if (row.isNew) {
      // Unsaved row — drop locally without a server round-trip.
      setRows((prev) => prev.filter((r) => r.rowId !== row.rowId));
      return;
    }
    if (!row.key.trim()) return;
    // Open the confirm dialog; the actual mutation runs from
    // ``confirmDelete`` once the user accepts.
    setConfirmTarget(row);
  };

  const confirmDelete = () => {
    const row = confirmTarget;
    if (!row) return;
    setConfirmTarget(null);
    setPendingId(row.rowId);
    deleteMutation.mutate(
      { key: row.key },
      {
        onSuccess: () => {
          toast.success(`Removed ${row.key}`);
          setPendingId(null);
        },
        onError: (err) => {
          toast.error(errMsg(err, "Remove failed"));
          setPendingId(null);
        },
      },
    );
  };

  return (
    <Card data-testid="envvars-editor-card">
      <CardHeader className="flex-row items-start justify-between gap-3 sm:items-center">
        <div className="flex flex-col gap-1.5">
          <CardTitle>Environment variables</CardTitle>
          <CardDescription>
            Edit the controller's persisted env. Sensitive keys are masked.
          </CardDescription>
        </div>
        <Button
          variant="secondary"
          onClick={handleAdd}
          data-testid="envvars-add"
        >
          <Plus aria-hidden className="size-3.5" />
          Add
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {envVars.isLoading ? (
          <div className="space-y-2" data-testid="envvars-loading">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : envVars.error ? (
          <div
            role="alert"
            data-testid="envvars-error"
            className="text-sm text-danger"
          >
            {envVars.error.message}
          </div>
        ) : rows.length === 0 ? (
          <p className="text-sm text-fg-muted" data-testid="envvars-empty">
            No environment variables. Press "Add" to create one.
          </p>
        ) : (
          <ul className="flex flex-col gap-2" data-testid="envvars-list">
            {rows.map((row) => {
              const isRevealed = revealed.has(row.rowId);
              const showMasked = row.sensitive && !isRevealed;
              return (
                <li
                  key={row.rowId}
                  data-testid={`envvars-row-${row.rowId}`}
                  className="flex flex-col gap-2 rounded-md border border-border bg-bg-1 p-3 sm:flex-row sm:items-center"
                >
                  <Input
                    aria-label="Variable key"
                    placeholder="KEY"
                    className="font-mono text-xs sm:max-w-[14rem]"
                    value={row.key}
                    onChange={(e) =>
                      updateRow(row.rowId, { key: e.target.value })
                    }
                    data-testid={`envvars-key-${row.rowId}`}
                    readOnly={!row.isNew}
                  />
                  <Input
                    aria-label="Variable value"
                    placeholder="value"
                    className="font-mono text-xs"
                    type={showMasked ? "password" : "text"}
                    value={showMasked ? "••••" : row.value}
                    onChange={(e) =>
                      updateRow(row.rowId, { value: e.target.value })
                    }
                    data-testid={`envvars-value-${row.rowId}`}
                    readOnly={showMasked}
                  />
                  <div className="flex items-center gap-2">
                    {row.sensitive ? (
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        onClick={() => toggleReveal(row.rowId)}
                        data-testid={`envvars-reveal-${row.rowId}`}
                        aria-label={
                          isRevealed
                            ? `Hide value for ${row.key}`
                            : `Reveal value for ${row.key}`
                        }
                        aria-pressed={isRevealed}
                      >
                        {isRevealed ? (
                          <EyeOff aria-hidden className="size-3.5" />
                        ) : (
                          <Eye aria-hidden className="size-3.5" />
                        )}
                        {isRevealed ? "Hide" : "Reveal"}
                      </Button>
                    ) : null}
                    <Button
                      type="button"
                      size="sm"
                      variant="primary"
                      onClick={() => handleSaveRow(row)}
                      loading={
                        pendingId === row.rowId && saveMutation.isPending
                      }
                      disabled={
                        pendingId === row.rowId && saveMutation.isPending
                      }
                      data-testid={`envvars-save-${row.rowId}`}
                    >
                      <Save aria-hidden className="size-3.5" />
                      Save
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      onClick={() => handleDeleteRow(row)}
                      loading={
                        pendingId === row.rowId && deleteMutation.isPending
                      }
                      disabled={
                        (pendingId === row.rowId && deleteMutation.isPending) ||
                        (row.isNew && !row.key.trim())
                      }
                      data-testid={`envvars-delete-${row.rowId}`}
                      aria-label={`Remove ${row.key || "row"}`}
                      className="text-fg-muted hover:text-danger"
                    >
                      <Trash2 aria-hidden className="size-3.5" />
                    </Button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
      <Dialog
        open={confirmTarget !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmTarget(null);
        }}
      >
        <DialogContent
          role="alertdialog"
          data-testid="envvars-delete-confirm"
          className="max-w-sm"
        >
          <DialogHeader>
            <DialogTitle>Remove environment variable?</DialogTitle>
            <DialogDescription>
              {confirmTarget
                ? `${confirmTarget.key} will be dropped from the controller process immediately. This does not edit the deployment manifest.`
                : ""}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => setConfirmTarget(null)}
              data-testid="envvars-delete-cancel"
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="danger"
              onClick={confirmDelete}
              data-testid="envvars-delete-confirm-button"
            >
              Remove
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
