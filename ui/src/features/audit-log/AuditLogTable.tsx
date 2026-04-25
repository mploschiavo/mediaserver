import { asArray } from "@/lib/coerce";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation } from "@tanstack/react-router";
import { motion, useReducedMotion } from "framer-motion";
import { AlertTriangle, ScrollText } from "lucide-react";
import { toast } from "sonner";
import type { AuditEntry } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { EmptyState } from "@/components/layout/EmptyState";
import {
  ResponsiveTable,
  type ResponsiveTableColumn,
} from "@/components/layout/ResponsiveTable";
import { SkeletonTable } from "@/components/layout/SkeletonTable";
import { useAuditLog } from "@/api/hooks";

/** Allowed page-size options. The list endpoint accepts arbitrary
 * positive integers, but we constrain the picker so operators can't
 * accidentally request a 100k-row dump. */
const LIMIT_OPTIONS = [50, 200, 1000] as const;
type LimitOption = (typeof LIMIT_OPTIONS)[number];

interface AuditLogRow {
  id: string;
  ts: string;
  actor: string;
  action: string;
  target: string;
  result: string;
  idempotencyKey: string;
  raw: AuditEntry;
}

/**
 * Read a string field off the permissive AuditEntry. The OpenAPI
 * shape is `additionalProperties: true`, so we coerce defensively
 * and fall back to "" rather than throwing on unexpected types.
 */
function pickString(entry: AuditEntry, ...keys: string[]): string {
  for (const key of keys) {
    const v = (entry as Record<string, unknown>)[key];
    if (typeof v === "string" && v.length > 0) return v;
  }
  return "";
}

/** Compress an ISO timestamp into a short relative phrase. */
function formatRelative(iso: string, now: number = Date.now()): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return iso;
  const delta = Math.max(0, Math.floor((now - t) / 1000));
  if (delta < 5) return "just now";
  if (delta < 60) return `${delta}s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

function truncate(value: string, max: number): string {
  if (value.length <= max) return value;
  return `${value.slice(0, max - 1)}…`;
}

function resultVariant(
  result: string,
): "success" | "danger" | "warning" | "default" {
  const r = result.toLowerCase();
  if (r === "ok" || r === "success") return "success";
  if (r === "fail" || r === "error" || r === "denied") return "danger";
  if (r === "warn" || r === "pending") return "warning";
  return "default";
}

function toRow(entry: AuditEntry, index: number): AuditLogRow {
  const ts = pickString(entry, "ts", "timestamp");
  const actor = pickString(entry, "actor") || "(system)";
  const action = pickString(entry, "action");
  const target = pickString(entry, "target");
  const result = pickString(entry, "result") || "ok";
  // The dashboard prototype surfaced `idempotency_key`; the
  // controller's actual entries don't include one. Keep both reads
  // permissive so the column renders whichever the server emits.
  const idempotencyKey =
    pickString(entry, "idempotency_key", "idempotencyKey") ||
    pickString((entry.detail ?? {}) as AuditEntry, "idempotency_key");

  return {
    id: `${ts || "row"}-${index}`,
    ts,
    actor,
    action,
    target,
    result,
    idempotencyKey,
    raw: entry,
  };
}

interface CopyableMonoProps {
  value: string;
  label: string;
}

/**
 * Mono, truncated value with a click-to-copy affordance + tooltip
 * showing the full string. Falls back to a static span when the
 * Clipboard API is unavailable (older Safari, sandboxed iframes).
 */
function CopyableMono({ value, label }: CopyableMonoProps) {
  const handleClick = useCallback(() => {
    if (!value) return;
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      navigator.clipboard
        .writeText(value)
        .then(() => toast.success(`${label} copied`))
        .catch(() => toast.error("Copy failed"));
    }
  }, [label, value]);

  if (!value) return <span className="text-fg-faint">—</span>;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          onClick={handleClick}
          className="max-w-[12ch] truncate font-mono text-xs text-fg-muted underline-offset-2 hover:text-fg hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-bg"
          data-testid="audit-log-copy"
          aria-label={`Copy ${label}`}
        >
          {truncate(value, 12)}
        </button>
      </TooltipTrigger>
      <TooltipContent>
        <span className="font-mono text-xs">{value}</span>
      </TooltipContent>
    </Tooltip>
  );
}

/**
 * Tamper-evident audit log table. The list endpoint
 * (`GET /api/audit-log`) is hash-chained server-side; the
 * IntegrityBanner companion verifies the chain on demand. This
 * component focuses on rendering rows with operator-friendly
 * affordances (relative timestamps, click-to-copy keys, action
 * filter prefix, page-size picker).
 */
export function AuditLogTable() {
  const reduce = useReducedMotion();
  const [limit, setLimit] = useState<LimitOption>(50);
  // Pre-fill the filter from the URL `?action=...` query param so
  // deep-links from sibling features (e.g. `/jobs`'s "Audit history"
  // button) drop the operator straight onto a filtered view. We read
  // through `useLocation()` rather than `useSearch()` so this hook
  // can mount under any route in tests without a strict `from:` arg.
  const location = useLocation();
  const initialAction = useMemo(() => {
    const search = (location.search ?? {}) as Record<string, unknown>;
    const raw = search.action;
    return typeof raw === "string" ? raw : "";
  }, [location.search]);
  const [actionFilter, setActionFilter] = useState(initialAction);

  // Keep the input in sync if the URL changes after mount (e.g.
  // operator clicks another deep-link from a sibling tab).
  useEffect(() => {
    setActionFilter(initialAction);
  }, [initialAction]);

  // The server-side `action` query param is a substring/prefix
  // filter (see `AuditLog.recent`'s `action_filter` arg). We thread
  // it directly to keep the wire round-trip authoritative.
  const trimmed = actionFilter.trim();
  const query = useAuditLog(
    trimmed ? { limit, action: trimmed } : { limit },
  );

  const rows = useMemo<AuditLogRow[]>(() => {
    const list = asArray(query.data?.entries);
    return list.map((entry, i) => toRow(entry, i));
  }, [query.data]);

  const columns: ResponsiveTableColumn<AuditLogRow>[] = [
    {
      id: "timestamp",
      header: "When",
      cell: (row) => (
        <Tooltip>
          <TooltipTrigger asChild>
            <span
              className="cursor-default tabular-nums text-fg-muted"
              data-testid={`audit-row-ts-${row.id}`}
            >
              {formatRelative(row.ts)}
            </span>
          </TooltipTrigger>
          <TooltipContent>
            <span className="font-mono text-xs">{row.ts || "—"}</span>
          </TooltipContent>
        </Tooltip>
      ),
    },
    {
      id: "actor",
      header: "Actor",
      cell: (row) => <span className="font-medium text-fg">{row.actor}</span>,
    },
    {
      id: "action",
      header: "Action",
      cell: (row) => (
        <span className="font-mono text-xs text-fg">{row.action || "—"}</span>
      ),
    },
    {
      id: "target",
      header: "Target",
      cell: (row) => (
        <Tooltip>
          <TooltipTrigger asChild>
            <span
              className="block max-w-[20ch] cursor-default truncate text-xs text-fg-muted"
              data-testid={`audit-row-target-${row.id}`}
            >
              {truncate(row.target || "—", 24)}
            </span>
          </TooltipTrigger>
          <TooltipContent>
            <span className="font-mono text-xs">{row.target || "—"}</span>
          </TooltipContent>
        </Tooltip>
      ),
    },
    {
      id: "result",
      header: "Result",
      cell: (row) => (
        <Badge variant={resultVariant(row.result)}>{row.result}</Badge>
      ),
    },
    {
      id: "idempotency-key",
      header: "Idempotency key",
      cell: (row) => (
        <CopyableMono value={row.idempotencyKey} label="Idempotency key" />
      ),
    },
  ];

  const filterControls = (
    <div
      className="flex flex-col gap-2 sm:flex-row sm:items-center"
      data-testid="audit-log-controls"
    >
      <Input
        type="text"
        value={actionFilter}
        onChange={(e) => setActionFilter(e.target.value)}
        placeholder="Filter by action prefix"
        aria-label="Filter audit log by action"
        data-testid="audit-log-filter"
        className="sm:max-w-xs"
      />
      <Select
        value={String(limit)}
        onValueChange={(value) => setLimit(Number(value) as LimitOption)}
      >
        <SelectTrigger
          className="sm:w-32"
          aria-label="Limit"
          data-testid="audit-log-limit-trigger"
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {LIMIT_OPTIONS.map((opt) => (
            <SelectItem key={opt} value={String(opt)}>
              {opt} rows
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );

  return (
    <motion.div
      initial={reduce ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
      className="flex flex-col gap-4"
      data-testid="audit-log-table"
    >
      {filterControls}

      {query.isLoading ? (
        <div data-testid="audit-log-loading">
          <SkeletonTable rows={6} columns={6} />
        </div>
      ) : query.error ? (
        <Card
          role="alert"
          data-testid="audit-log-error"
          className="border-[color-mix(in_oklab,var(--color-danger)_40%,transparent)]"
        >
          <CardContent className="flex flex-col gap-3 p-6">
            <div className="flex items-center gap-2 text-danger">
              <AlertTriangle aria-hidden className="size-4" />
              <span className="font-medium">Could not load audit log</span>
            </div>
            <p className="text-sm text-fg-muted">{query.error.message}</p>
            <div>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => void query.refetch()}
                data-testid="audit-log-retry"
              >
                Retry
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : rows.length === 0 ? (
        <EmptyState
          icon={ScrollText}
          title={trimmed ? "No matching entries" : "No audit entries yet"}
          description={
            trimmed
              ? `Nothing matches the "${trimmed}" action filter.`
              : "Operator actions will appear here as they're recorded."
          }
        />
      ) : (
        <Card className="p-0">
          <ResponsiveTable
            rows={rows}
            rowKey={(r) => r.id}
            columns={columns}
            card={(row) => (
              <div
                className="flex flex-col gap-2"
                data-testid={`audit-card-${row.id}`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium text-fg">{row.actor}</span>
                  <Badge variant={resultVariant(row.result)}>
                    {row.result}
                  </Badge>
                </div>
                <span className="font-mono text-xs text-fg">
                  {row.action || "—"}
                </span>
                <span className="text-xs text-fg-muted">
                  {truncate(row.target || "—", 64)}
                </span>
                <div className="flex items-center justify-between text-xs text-fg-muted">
                  <span className="tabular-nums">
                    {formatRelative(row.ts)}
                  </span>
                  <CopyableMono
                    value={row.idempotencyKey}
                    label="Idempotency key"
                  />
                </div>
              </div>
            )}
          />
        </Card>
      )}
    </motion.div>
  );
}
