import { useEffect, useState, type FormEvent, type JSX } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
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
import {
  useAddSchedule,
  useUpdateSchedule,
  type ScheduleShape,
} from "./hooks";

const MIN_INTERVAL_SECONDS = 60;

interface ScheduleEditorModalProps {
  open: boolean;
  /** When non-null, the modal is in edit mode for this schedule.
   *  When null, the modal creates a new schedule. */
  editing: ScheduleShape | null;
  onClose: () => void;
}

/**
 * Create / edit dialog for schedules. Pattern matches
 * ``ResetPasswordDialog`` (Radix Dialog + manual form state +
 * useMutation + toast). Three editable fields:
 *
 *   * action — the job/action name to fire (free text; future work
 *     could pull a dropdown from ``GET /api/jobs``)
 *   * interval_seconds — cadence; surface as a helper "every Nm/h/d"
 *     shortcut alongside the raw seconds field
 *   * enabled — toggle that lets the operator create a paused
 *     schedule directly (or pause an existing one without touching
 *     the rest of the row)
 *
 * Cron expressions are deferred to a follow-up — interval seconds
 * matches the existing schedule shape and avoids a new dep
 * (``croniter``). The shortcut helper covers the common cases.
 */
export function ScheduleEditorModal({
  open,
  editing,
  onClose,
}: ScheduleEditorModalProps): JSX.Element {
  const [action, setAction] = useState("");
  const [intervalSeconds, setIntervalSeconds] = useState<number>(3600);
  const [label, setLabel] = useState("");
  const [enabled, setEnabled] = useState(true);

  // Reset / hydrate the form whenever the modal opens. Using a key
  // off ``editing?.id ?? "create"`` would also work, but resetting
  // via effect keeps the component a single mount per session and
  // avoids tearing down the Dialog's portal on every open.
  useEffect(() => {
    if (!open) return;
    if (editing) {
      setAction(editing.action);
      setIntervalSeconds(editing.interval_seconds);
      setLabel(editing.label);
      setEnabled(editing.enabled);
    } else {
      setAction("");
      setIntervalSeconds(3600);
      setLabel("");
      setEnabled(true);
    }
  }, [open, editing]);

  const addSched = useAddSchedule();
  const updateSched = useUpdateSchedule();

  const handleSubmit = (ev: FormEvent) => {
    ev.preventDefault();
    if (!action.trim()) {
      toast.error("Action is required");
      return;
    }
    if (intervalSeconds < MIN_INTERVAL_SECONDS) {
      toast.error(
        `Interval must be at least ${MIN_INTERVAL_SECONDS} seconds`,
      );
      return;
    }
    const onSuccess = () => {
      toast.success(editing ? "Schedule updated" : "Schedule created");
      onClose();
    };
    const onError = (err: Error) => {
      toast.error(`${editing ? "Update" : "Create"} failed: ${err.message}`);
    };
    if (editing) {
      void updateSched.mutate(
        {
          schedule_id: editing.id,
          action: action.trim(),
          interval_seconds: intervalSeconds,
          label: label.trim() || `${action.trim()} every ${intervalSeconds}s`,
          enabled,
        },
        { onSuccess, onError },
      );
    } else {
      void addSched.mutate(
        {
          action: action.trim(),
          interval_seconds: intervalSeconds,
          label: label.trim() || `${action.trim()} every ${intervalSeconds}s`,
          enabled,
        },
        { onSuccess, onError },
      );
    }
  };

  const isPending = addSched.isPending || updateSched.isPending;

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent data-testid="schedule-editor-modal">
        <DialogHeader>
          <DialogTitle>
            {editing ? "Edit schedule" : "New schedule"}
          </DialogTitle>
          <DialogDescription>
            Recurring jobs run on the controller. Cadence is in
            seconds; minimum 60.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={handleSubmit}
          data-testid="schedule-editor-form"
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="schedule-action">Action</Label>
            <Input
              id="schedule-action"
              value={action}
              onChange={(e) => setAction(e.target.value)}
              placeholder="scan-completed-downloads"
              data-testid="schedule-editor-action"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="schedule-interval">Interval (seconds)</Label>
            <Input
              id="schedule-interval"
              type="number"
              min={MIN_INTERVAL_SECONDS}
              value={intervalSeconds}
              onChange={(e) =>
                setIntervalSeconds(Number(e.target.value) || 0)
              }
              data-testid="schedule-editor-interval"
            />
            <span className="text-xs text-fg-muted">
              Helpers: 60s = 1m, 3600s = 1h, 86400s = 1d
            </span>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="schedule-label">Label (optional)</Label>
            <Input
              id="schedule-label"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder={`${action || "job"} every ${intervalSeconds}s`}
              data-testid="schedule-editor-label"
            />
          </div>
          <div className="flex items-center gap-2">
            <input
              id="schedule-enabled"
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              data-testid="schedule-editor-enabled"
            />
            <Label htmlFor="schedule-enabled" className="cursor-pointer">
              Enabled (uncheck to create paused)
            </Label>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="secondary"
              onClick={onClose}
              data-testid="schedule-editor-cancel"
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              loading={isPending}
              data-testid="schedule-editor-save"
            >
              {editing ? "Save" : "Create"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
