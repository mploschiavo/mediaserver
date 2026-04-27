import { useMemo, useState, type JSX } from "react";
import { Pause, Pencil, Play, Plus, Trash2 } from "lucide-react";
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
  useDeleteSchedule,
  usePauseSchedule,
  useResumeSchedule,
  useSchedules,
  type ScheduleShape,
} from "./hooks";
import { ScheduleEditorModal } from "./ScheduleEditorModal";

/**
 * "Schedules (catalog)" card per design doc §3 lines 182-192.
 * Lists every persisted schedule grouped by the action's namespace
 * prefix (e.g. ``media-integrity:scan`` → "media-integrity"; bare
 * ``scan-completed-downloads`` → "general"). Each row exposes a
 * pause/resume toggle, an edit pencil, and a destructive remove
 * affordance; "+ Schedule" opens the modal in create mode.
 *
 * Live updates ride the Phase 2 SSE bus. Add/update/pause/resume/
 * delete mutations all invalidate the ``["schedules"]`` query key
 * so the list re-fetches without explicit refetch calls.
 */
export function SchedulesCard(): JSX.Element {
  const q = useSchedules();
  const [editing, setEditing] = useState<ScheduleShape | null>(null);
  const [creating, setCreating] = useState(false);
  const pause = usePauseSchedule();
  const resume = useResumeSchedule();
  const remove = useDeleteSchedule();

  const grouped = useMemo(() => groupSchedules(q.data?.schedules ?? []), [
    q.data,
  ]);

  return (
    <Card data-testid="schedules-card">
      <CardHeader className="flex flex-row items-center justify-between gap-2 pb-3">
        <CardTitle className="text-sm">Schedules</CardTitle>
        <Button
          type="button"
          size="sm"
          variant="primary"
          onClick={() => setCreating(true)}
          data-testid="schedules-add"
        >
          <Plus aria-hidden className="size-3.5" />
          Schedule
        </Button>
      </CardHeader>
      <CardContent>
        {q.isLoading ? (
          <div className="flex flex-col gap-2" data-testid="schedules-loading">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        ) : q.error ? (
          <p
            role="alert"
            className="text-sm text-danger"
            data-testid="schedules-error"
          >
            Couldn&apos;t load schedules: {(q.error as Error).message}
          </p>
        ) : (q.data?.count ?? 0) === 0 ? (
          <p className="text-sm text-fg-faint" data-testid="schedules-empty">
            No schedules configured. Click <span className="font-mono">+ Schedule</span> to create one.
          </p>
        ) : (
          <div className="flex flex-col gap-3" data-testid="schedules-list">
            {grouped.map(([group, items]) => (
              <div key={group}>
                <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-fg-muted">
                  {group}
                </p>
                <ul className="flex flex-col gap-1">
                  {items.map((s) => (
                    <ScheduleRow
                      key={s.id}
                      schedule={s}
                      onEdit={() => setEditing(s)}
                      onPause={() => {
                        void pause.mutate(s.id, {
                          onSuccess: () => toast.success(`Paused ${s.action}`),
                          onError: (err) =>
                            toast.error(`Pause failed: ${err.message}`),
                        });
                      }}
                      onResume={() => {
                        void resume.mutate(s.id, {
                          onSuccess: () => toast.success(`Resumed ${s.action}`),
                          onError: (err) =>
                            toast.error(`Resume failed: ${err.message}`),
                        });
                      }}
                      onDelete={() => {
                        if (
                          !window.confirm(
                            `Remove the schedule "${s.label}"?`,
                          )
                        ) return;
                        void remove.mutate(s.id, {
                          onSuccess: () =>
                            toast.success(`Removed ${s.action}`),
                          onError: (err) =>
                            toast.error(`Remove failed: ${err.message}`),
                        });
                      }}
                    />
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </CardContent>
      <ScheduleEditorModal
        open={creating || editing !== null}
        editing={editing}
        onClose={() => {
          setEditing(null);
          setCreating(false);
        }}
      />
    </Card>
  );
}

function ScheduleRow({
  schedule,
  onEdit,
  onPause,
  onResume,
  onDelete,
}: {
  schedule: ScheduleShape;
  onEdit: () => void;
  onPause: () => void;
  onResume: () => void;
  onDelete: () => void;
}): JSX.Element {
  return (
    <li
      className="flex items-center gap-2 rounded-md border border-border bg-bg-1 px-2 py-1.5 text-xs"
      data-testid={`schedule-row-${schedule.id}`}
      data-enabled={schedule.enabled ? "true" : "false"}
    >
      <Button
        type="button"
        size="sm"
        variant="ghost"
        onClick={schedule.enabled ? onPause : onResume}
        aria-label={
          schedule.enabled
            ? `Pause schedule ${schedule.action}`
            : `Resume schedule ${schedule.action}`
        }
        data-testid={`schedule-toggle-${schedule.id}`}
      >
        {schedule.enabled ? (
          <Pause aria-hidden className="size-3" />
        ) : (
          <Play aria-hidden className="size-3" />
        )}
      </Button>
      <span className="flex-1 truncate font-medium text-fg">
        {schedule.label}
      </span>
      <span className="font-mono tabular-nums text-fg-muted">
        every {formatInterval(schedule.interval_seconds)}
      </span>
      {!schedule.enabled ? (
        <Badge variant="warning" className="px-1.5 py-0 text-[10px]">
          paused
        </Badge>
      ) : null}
      <Button
        type="button"
        size="sm"
        variant="ghost"
        onClick={onEdit}
        aria-label={`Edit schedule ${schedule.action}`}
        data-testid={`schedule-edit-${schedule.id}`}
      >
        <Pencil aria-hidden className="size-3" />
      </Button>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        onClick={onDelete}
        aria-label={`Remove schedule ${schedule.action}`}
        data-testid={`schedule-remove-${schedule.id}`}
      >
        <Trash2 aria-hidden className="size-3" />
      </Button>
    </li>
  );
}

function formatInterval(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.round(hours / 24);
  return `${days}d`;
}

function groupSchedules(
  schedules: readonly ScheduleShape[],
): readonly [string, readonly ScheduleShape[]][] {
  const groups = new Map<string, ScheduleShape[]>();
  for (const s of schedules) {
    const group = s.action.includes(":")
      ? s.action.split(":", 1)[0] ?? "general"
      : "general";
    const list = groups.get(group) ?? [];
    list.push(s);
    groups.set(group, list);
  }
  return Array.from(groups.entries()).sort((a, b) =>
    a[0].localeCompare(b[0]),
  );
}
