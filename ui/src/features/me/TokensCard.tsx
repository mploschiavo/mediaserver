import { asArray } from "@/lib/coerce";
import { useCallback, useId, useMemo, useState } from "react";
import { Copy, KeyRound, Trash2 } from "lucide-react";
import { toast } from "sonner";
import type { ColumnDef } from "@tanstack/react-table";
import { ApiError } from "@/api";
import { ApiErrorTile } from "@/components/ApiErrorTile";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { DataTable } from "@/components/data-table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { formatRelative } from "@/features/media-integrity/format";
import {
  useGenerateToken,
  useMeTokens,
  useRevokeToken,
  type MeToken,
} from "./hooks";

type DialogStage = "form" | "reveal";

function tokenId(t: MeToken): string {
  return String(t.token_id ?? t.id ?? "");
}

function extractRaw(res: unknown): string {
  if (!res || typeof res !== "object") return "";
  const r = res as Record<string, unknown>;
  for (const key of ["token", "access_token", "secret", "raw"] as const) {
    const v = r[key];
    if (typeof v === "string" && v.length > 0) return v;
  }
  return "";
}

function errMsg(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}

async function writeClipboard(text: string): Promise<boolean> {
  try {
    if (
      typeof navigator !== "undefined" &&
      navigator.clipboard &&
      typeof navigator.clipboard.writeText === "function"
    ) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through
  }
  return false;
}

/**
 * Tokens card for the /me route. Shows the operator's existing tokens
 * and a "Generate new token" flow. The raw token is displayed once in
 * the reveal stage of the dialog; it is stored only in local component
 * state and is cleared (set to empty string) when the dialog closes or
 * the "I've stored it" button is pressed. We never cache or log the
 * raw token beyond that lifetime.
 */
export function TokensCard() {
  const nameFieldId = useId();
  const scopesFieldId = useId();
  const expiresFieldId = useId();

  const tokensQuery = useMeTokens();
  const generate = useGenerateToken();
  const revoke = useRevokeToken();

  const [open, setOpen] = useState(false);
  const [stage, setStage] = useState<DialogStage>("form");
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState("");
  const [expiresAt, setExpiresAt] = useState("");
  // Raw token lives here for the reveal stage only. Replaced with ""
  // on dismiss so the sensitive value is garbage-collected.
  const [raw, setRaw] = useState("");

  const tokens = asArray(tokensQuery.data?.tokens);

  const resetForm = useCallback(() => {
    setName("");
    setScopes("");
    setExpiresAt("");
  }, []);

  const dismiss = useCallback(() => {
    setOpen(false);
    setStage("form");
    setRaw("");
    resetForm();
  }, [resetForm]);

  const handleOpenChange = useCallback(
    (next: boolean) => {
      if (!next) {
        // Guaranteed to wipe the raw token if the user ESC/clicks
        // outside the modal mid-reveal.
        dismiss();
      } else {
        setOpen(true);
      }
    },
    [dismiss],
  );

  const handleGenerate = useCallback(() => {
    if (generate.isPending || !name.trim()) return;
    const scopeList = scopes
      .split(/[\s,]+/)
      .map((s) => s.trim())
      .filter((s) => s.length > 0);
    const payload: {
      name: string;
      scopes?: readonly string[];
      expires_at?: string;
    } = { name: name.trim() };
    if (scopeList.length) payload.scopes = scopeList;
    if (expiresAt) payload.expires_at = expiresAt;
    generate.mutate(payload, {
      onSuccess: (res) => {
        const rawToken = extractRaw(res);
        if (rawToken) {
          setRaw(rawToken);
          setStage("reveal");
          toast.success("Token issued. Copy it now — it won't be shown again.");
        } else {
          // Fallback: the server issued but didn't return the raw
          // value. Nothing to reveal; close and let the list refresh.
          toast.success("Token issued.");
          dismiss();
        }
      },
      onError: (err) =>
        toast.error(errMsg(err, "Token generation failed")),
    });
  }, [dismiss, expiresAt, generate, name, scopes]);

  const handleCopy = useCallback(async () => {
    if (!raw) return;
    const ok = await writeClipboard(raw);
    if (ok) toast.success("Copied to clipboard");
    else toast.error("Clipboard unavailable — copy the value manually.");
  }, [raw]);

  const handleRevoke = useCallback(
    (id: string) => {
      if (!id || revoke.isPending) return;
      revoke.mutate(id, {
        onSuccess: () => toast.success("Token revoked"),
        onError: (err) => toast.error(errMsg(err, "Revoke failed")),
      });
    },
    [revoke],
  );

  // Memoise columns so the DataTable's TanStack table instance doesn't
  // tear down on every parent re-render. `revoke.isPending` and
  // `handleRevoke` are the only deps that gate the actions cell — the
  // rest are pure renderers.
  const columns = useMemo<ColumnDef<MeToken>[]>(
    () => [
      {
        id: "name",
        accessorFn: (row) => row.name ?? "(unnamed)",
        header: "Name",
        meta: { label: "Name" },
        cell: ({ row }) => (
          <span className="font-medium text-fg">
            {row.original.name ?? "(unnamed)"}
          </span>
        ),
      },
      {
        id: "scopes",
        accessorFn: (row) => (row.scopes ?? []).join(" "),
        header: "Scopes",
        meta: { label: "Scopes" },
        cell: ({ row }) => {
          const list = row.original.scopes ?? [];
          if (list.length === 0) {
            return <span className="text-xs text-fg-faint">—</span>;
          }
          return (
            <div className="flex flex-wrap gap-1">
              {list.map((s) => (
                <Badge key={s} variant="default">
                  {s}
                </Badge>
              ))}
            </div>
          );
        },
      },
      {
        id: "created_at",
        accessorFn: (row) => row.created_at ?? "",
        header: "Created",
        meta: { label: "Created" },
        enableColumnFilter: false,
        cell: ({ row }) => (
          <span className="text-xs tabular-nums text-fg-muted">
            {formatRelative(row.original.created_at ?? "")}
          </span>
        ),
      },
      {
        id: "last_used_at",
        accessorFn: (row) => row.last_used_at ?? "",
        header: "Last used",
        meta: { label: "Last used" },
        enableColumnFilter: false,
        cell: ({ row }) => (
          <span className="text-xs tabular-nums text-fg-muted">
            {row.original.last_used_at
              ? formatRelative(row.original.last_used_at)
              : "never"}
          </span>
        ),
      },
      {
        id: "expires_at",
        accessorFn: (row) => row.expires_at ?? "",
        header: "Expires",
        meta: { label: "Expires" },
        enableColumnFilter: false,
        cell: ({ row }) => (
          <span className="text-xs tabular-nums text-fg-muted">
            {row.original.expires_at ?? "never"}
          </span>
        ),
      },
      {
        id: "actions",
        header: "Actions",
        meta: { label: "Actions" },
        enableSorting: false,
        enableColumnFilter: false,
        cell: ({ row }) => {
          const id = tokenId(row.original);
          return (
            <div className="flex items-center justify-end">
              <Button
                size="sm"
                variant="secondary"
                onClick={() => handleRevoke(id)}
                disabled={revoke.isPending || !id}
                data-testid={`token-revoke-${id}`}
              >
                <Trash2 aria-hidden className="size-3.5" />
                Revoke
              </Button>
            </div>
          );
        },
      },
    ],
    [handleRevoke, revoke.isPending],
  );

  return (
    <>
      <Card data-testid="tokens-card">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <KeyRound aria-hidden className="size-4" />
            API tokens
          </CardTitle>
          <CardDescription>Personal access tokens.</CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {tokensQuery.isLoading ? (
            <div className="space-y-2 p-6" data-testid="tokens-card-loading">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : tokensQuery.error ? (
            <div className="px-6 py-4" data-testid="tokens-card-error">
              <ApiErrorTile
                error={tokensQuery.error}
                onRetry={() => void tokensQuery.refetch()}
              />
            </div>
          ) : tokens.length === 0 ? (
            <p
              className="px-6 py-4 text-sm text-fg-muted"
              data-testid="tokens-card-empty"
            >
              No tokens issued.
            </p>
          ) : (
            <div className="px-6 pb-6">
              <DataTable<MeToken>
                testId="tokens-table"
                columns={columns}
                data={tokens}
                getRowId={(row) => tokenId(row)}
                caption={`${tokens.length} token${tokens.length === 1 ? "" : "s"}`}
                emptyState="No tokens issued."
              />
            </div>
          )}
        </CardContent>
        <div className="flex items-center justify-end border-t border-border px-6 py-4">
          <Button
            variant="secondary"
            onClick={() => setOpen(true)}
            data-testid="generate-token"
          >
            Generate new token
          </Button>
        </div>
      </Card>

      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent data-testid="generate-token-dialog">
          {stage === "form" ? (
            <>
              <DialogHeader>
                <DialogTitle>Generate API token</DialogTitle>
                <DialogDescription>
                  The raw token will only be shown once. Copy it somewhere
                  safe immediately.
                </DialogDescription>
              </DialogHeader>
              <div className="flex flex-col gap-3">
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor={nameFieldId}>Name</Label>
                  <Input
                    id={nameFieldId}
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="e.g. CI deploy"
                    autoFocus
                    data-testid="generate-token-name"
                  />
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor={scopesFieldId}>
                    Scopes
                    <span className="ml-1 font-normal text-fg-faint">
                      (space- or comma-separated)
                    </span>
                  </Label>
                  <Input
                    id={scopesFieldId}
                    value={scopes}
                    onChange={(e) => setScopes(e.target.value)}
                    placeholder="read write"
                    data-testid="generate-token-scopes"
                  />
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor={expiresFieldId}>
                    Expires{" "}
                    <span className="font-normal text-fg-faint">
                      (optional)
                    </span>
                  </Label>
                  <Input
                    id={expiresFieldId}
                    type="datetime-local"
                    value={expiresAt}
                    onChange={(e) => setExpiresAt(e.target.value)}
                    data-testid="generate-token-expires"
                  />
                </div>
              </div>
              <DialogFooter>
                <Button variant="secondary" onClick={dismiss}>
                  Cancel
                </Button>
                <Button
                  variant="primary"
                  onClick={handleGenerate}
                  loading={generate.isPending}
                  disabled={!name.trim() || generate.isPending}
                  data-testid="generate-token-submit"
                >
                  Generate
                </Button>
              </DialogFooter>
            </>
          ) : (
            <>
              <DialogHeader>
                <DialogTitle>Your new token</DialogTitle>
                <DialogDescription>
                  Copy this value now. It will never be shown again — if
                  you lose it, you'll need to generate a new one.
                </DialogDescription>
              </DialogHeader>
              <div
                className="flex items-center gap-2 rounded-md border border-border bg-bg-2 px-3 py-2 font-mono text-xs text-fg break-all"
                data-testid="generate-token-raw"
              >
                <span className="flex-1">{raw}</span>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={handleCopy}
                  data-testid="generate-token-copy"
                  aria-label="Copy token to clipboard"
                >
                  <Copy aria-hidden className="size-3.5" />
                  Copy
                </Button>
              </div>
              <DialogFooter>
                <Button
                  variant="primary"
                  onClick={dismiss}
                  data-testid="generate-token-dismiss"
                >
                  I've stored it
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
