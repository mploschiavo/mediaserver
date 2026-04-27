import { useState } from "react";
import { ChevronRight, Loader2, Square } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { cn } from "@/lib/cn";
import {
  useCancelAction,
  useJobsRunning,
  type RunningTreeNodeShape,
} from "./hooks";
import { formatElapsed } from "./format";
import { RunDrawer } from "./RunDrawer";

/**
 * "Currently running" card per docs/design/ux-polish-backlog-mockups
 * §3 (lines 162-200). Renders the in-flight run tree with the design
 * doc's ``▶/⏵/✓/✗`` glyph vocabulary, per-step elapsed counters, and
 * an inline Cancel control on each running top-level node.
 *
 * Hidden when nothing is running so the JobsPage stays quiet during
 * normal operation. Live updates ride the unified SSE bus — the
 * EventStreamProvider invalidates ``["jobs"]`` on every job.* event,
 * which triggers this card's hook to refetch immediately rather than
 * waiting for the 5s poll fallback.
 *
 * Clicking any node opens the existing RunDrawer for full detail
 * (status, stdout_tail, log_anchor deep-link, children); the drawer
 * is shared with the row-click flow on RunHistoryPanel + LastRunPanel
 * so the operator sees the same surface from any entry point.
 */
const STATUS_GLYPH: Record<string, string> = {
  running: "▶", // ▶
  ok: "✓", // ✓
  skipped: "⧖", // ⧖
  error: "✗", // ✗
  cancelled: "⦰", // ⦰
  timeout: "⏱", // ⏱
};
const STATUS_GLYPH_PENDING = "⏵"; // ⏵

const STATUS_TONE: Record<string, "info" | "success" | "warning" | "danger" | "default" | "outline"> = {
  running: "info",
  ok: "success",
  skipped: "warning",
  error: "danger",
  cancelled: "outline",
  timeout: "danger",
};

function statusGlyph(status: string): string {
  return STATUS_GLYPH[status] ?? STATUS_GLYPH_PENDING;
}

function statusTone(
  status: string,
): "info" | "success" | "warning" | "danger" | "default" | "outline" {
  return STATUS_TONE[status] ?? "default";
}

export function CurrentlyRunningCard(): JSX.Element | null {
  const q = useJobsRunning();
  const [drawerRunId, setDrawerRunId] = useState<string | null>(null);
  const tree = q.data?.tree ?? [];

  // Hidden when nothing is running — the design calls for this
  // (line 153: "Bootstrap is running according to the banner — which
  // step?" implies we surface only when there IS something).
  if (q.isLoading || tree.length === 0) return null;

  return (
    <Card data-testid="currently-running-card">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Loader2
            aria-hidden
            className="size-4 animate-spin text-info"
          />
          Currently running
          <Badge variant="info" data-testid="currently-running-count">
            {tree.length}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <ul className="flex flex-col gap-2" data-testid="currently-running-tree">
          {tree.map((node) => (
            <RunningNode
              key={node.run_id}
              node={node}
              depth={0}
              onSelect={setDrawerRunId}
            />
          ))}
        </ul>
      </CardContent>
      <RunDrawer
        runId={drawerRunId}
        onClose={() => setDrawerRunId(null)}
        onSelectRunId={(id) => setDrawerRunId(id)}
      />
    </Card>
  );
}

function RunningNode({
  node,
  depth,
  onSelect,
}: {
  node: RunningTreeNodeShape;
  depth: number;
  onSelect: (runId: string) => void;
}): JSX.Element {
  return (
    <li
      data-testid={`running-node-${node.run_id}`}
      data-status={node.status}
      data-depth={depth}
    >
      <div
        className="flex items-center gap-2 rounded-md border border-border bg-bg-1 pr-1"
        style={{ marginLeft: depth * 18 }}
      >
        <button
          type="button"
          onClick={() => onSelect(node.run_id)}
          className={cn(
            "flex flex-1 items-center gap-2 px-2 py-1.5 text-left text-xs",
            "[@media(hover:hover)]:hover:bg-bg-2",
          )}
          data-testid={`running-node-button-${node.run_id}`}
        >
          <span
            aria-hidden
            className="font-mono text-info"
            data-testid={`running-node-glyph-${node.run_id}`}
          >
            {statusGlyph(node.status)}
          </span>
          <span className="flex-1 truncate font-medium text-fg">
            {node.job_name}
          </span>
          <Badge
            variant={statusTone(node.status)}
            className="px-1.5 py-0 text-[10px]"
          >
            {node.status}
          </Badge>
          <span
            className="font-mono tabular-nums text-fg-muted"
            data-testid={`running-node-elapsed-${node.run_id}`}
          >
            {formatElapsed(node.elapsed_seconds)}
          </span>
          <span className="font-mono text-[10px] text-fg-faint">
            {node.triggered_by}
          </span>
          <ChevronRight aria-hidden className="size-3 text-fg-faint" />
        </button>
        {depth === 0 ? <CancelControl runId={node.run_id} /> : null}
      </div>
      {node.children.length > 0 ? (
        <ul className="mt-1 flex flex-col gap-1">
          {node.children.map((child) => (
            <RunningNode
              key={child.run_id}
              node={child}
              depth={depth + 1}
              onSelect={onSelect}
            />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function CancelControl({ runId }: { runId: string }): JSX.Element {
  const cancel = useCancelAction();
  return (
    <Button
      type="button"
      size="sm"
      variant="ghost"
      onClick={() => {
        // Explicit ``void`` keeps the floating-promises ratchet
        // happy (the regex flags any bare ``mutate(`` call as a
        // potential leaked promise; this one's intentional fire-
        // and-forget — completion is reported via toast).
        void cancel.mutate(undefined, {
          onSuccess: () => toast.success("Cancel signal sent"),
          onError: (err) =>
            toast.error(
              `Cancel failed: ${err instanceof Error ? err.message : "unknown"}`,
            ),
        });
      }}
      disabled={cancel.isPending}
      data-testid={`running-node-cancel-${runId}`}
      aria-label={`Cancel running job ${runId}`}
    >
      {cancel.isPending ? (
        <Loader2 aria-hidden className="size-3 animate-spin" />
      ) : (
        <Square aria-hidden className="size-3" />
      )}
      Cancel
    </Button>
  );
}

