import type { JSX } from "react";
import { ArrowDown, ArrowUp, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useJobQueue,
  useRemoveQueueEntry,
  useReorderQueueEntry,
  type QueueEntryShape,
} from "./hooks";

/**
 * Operator-managed pending-work queue per design doc §3 lines 176-180.
 * Each row carries the queued job's label, source chip, and inline
 * `[↑][↓][×]` controls. Drag-and-drop is intentionally deferred — the
 * arrow buttons are accessibility-friendly first; DnD is a follow-up
 * if operators ask for it.
 *
 * Hidden when the queue is empty; the page stays quiet during normal
 * operation. Polling cadence is 10s (with 2s stale window); SSE-driven
 * invalidation flips this to instant-refresh once the publishers wire
 * a ``queue.changed`` event in the deferred follow-up.
 */
export function QueueCard(): JSX.Element | null {
  const q = useJobQueue();
  const remove = useRemoveQueueEntry();
  const reorder = useReorderQueueEntry();
  const entries = q.data?.queue ?? [];

  if (q.isLoading) {
    return (
      <Card data-testid="queue-card-loading">
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Queue</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col gap-2">
            {[0, 1].map((i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }
  if (entries.length === 0) return null;

  return (
    <Card data-testid="queue-card">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-sm">
          Queue
          <Badge variant="info" data-testid="queue-count">
            {entries.length}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <ol
          className="flex flex-col gap-1"
          data-testid="queue-list"
        >
          {entries.map((entry, idx) => (
            <QueueRow
              key={entry.id}
              index={idx}
              total={entries.length}
              entry={entry}
              onMoveUp={() => {
                void reorder.mutate(
                  { entry_id: entry.id, direction: "up" },
                  {
                    onError: (err) =>
                      toast.error(`Reorder failed: ${err.message}`),
                  },
                );
              }}
              onMoveDown={() => {
                void reorder.mutate(
                  { entry_id: entry.id, direction: "down" },
                  {
                    onError: (err) =>
                      toast.error(`Reorder failed: ${err.message}`),
                  },
                );
              }}
              onRemove={() => {
                void remove.mutate(entry.id, {
                  onSuccess: () =>
                    toast.success(`Removed ${entry.job_name} from queue`),
                  onError: (err) =>
                    toast.error(`Remove failed: ${err.message}`),
                });
              }}
            />
          ))}
        </ol>
      </CardContent>
    </Card>
  );
}

function QueueRow({
  index,
  total,
  entry,
  onMoveUp,
  onMoveDown,
  onRemove,
}: {
  index: number;
  total: number;
  entry: QueueEntryShape;
  onMoveUp: () => void;
  onMoveDown: () => void;
  onRemove: () => void;
}): JSX.Element {
  return (
    <li
      className="flex items-center gap-2 rounded-md border border-border bg-bg-1 px-2 py-1.5 text-xs"
      data-testid={`queue-row-${entry.id}`}
      data-position={index}
    >
      <span className="font-mono tabular-nums text-fg-muted">
        #{index + 1}
      </span>
      <span className="flex-1 truncate font-medium text-fg">
        {entry.label}
      </span>
      <Badge variant="outline" className="px-1.5 py-0 text-[10px]">
        {entry.source}
      </Badge>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        onClick={onMoveUp}
        disabled={index === 0}
        aria-label={`Move ${entry.job_name} up`}
        data-testid={`queue-up-${entry.id}`}
      >
        <ArrowUp aria-hidden className="size-3" />
      </Button>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        onClick={onMoveDown}
        disabled={index === total - 1}
        aria-label={`Move ${entry.job_name} down`}
        data-testid={`queue-down-${entry.id}`}
      >
        <ArrowDown aria-hidden className="size-3" />
      </Button>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        onClick={onRemove}
        aria-label={`Remove ${entry.job_name} from queue`}
        data-testid={`queue-remove-${entry.id}`}
      >
        <Trash2 aria-hidden className="size-3" />
      </Button>
    </li>
  );
}
