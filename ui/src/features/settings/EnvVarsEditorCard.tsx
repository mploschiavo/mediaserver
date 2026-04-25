import { useEffect, useMemo, useState } from "react";
import { Eye, EyeOff, Plus, Save } from "lucide-react";
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
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { asArray } from "@/lib/coerce";
import { useQueryClient, useMutation } from "@tanstack/react-query";
import {
  isSensitiveKey,
  settingsKeys,
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

  const initial = useMemo(() => fromServer(envVars.data), [envVars.data]);
  const [rows, setRows] = useState<EditableRow[]>([]);
  const { revealed, toggle: toggleReveal } = useRevealedSet();
  const [pendingId, setPendingId] = useState<string | null>(null);

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
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
