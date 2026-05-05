import { useState } from "react";
import {
  AlertTriangle,
  Lock,
  Pause,
  Play,
  Trash2,
  Zap,
} from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
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
import {
  useEngageLockdown,
  useForceEvaluate,
  usePauseGuardrails,
  useReleaseLockdown,
  useRunCleanup,
  type DiskGuardrailState,
} from "./hooks";

interface StorageActionButtonsProps {
  state: DiskGuardrailState;
  /** When true, every mutating button is disabled with a tooltip. */
  readOnly?: boolean;
}

function explain(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}

const READ_ONLY_TOOLTIP = "You need controller_admin to run this action";

export function StorageActionButtons({
  state,
  readOnly = false,
}: StorageActionButtonsProps) {
  const cleanup = useRunCleanup();
  const engage = useEngageLockdown();
  const release = useReleaseLockdown();
  const pause = usePauseGuardrails();
  const evaluate = useForceEvaluate();

  const [confirm, setConfirm] = useState<
    null | "cleanup" | "engage" | "release"
  >(null);
  const [pauseOpen, setPauseOpen] = useState(false);
  const [pauseHours, setPauseHours] = useState("1");

  const isLocked = state === "AUTO_LOCKDOWN" || state === "MANUAL_LOCKDOWN";
  const engageDisabled = readOnly || isLocked || engage.isPending;
  const releaseDisabled = readOnly || state === "NORMAL" || release.isPending;
  const cleanupDisabled = readOnly || cleanup.isPending;
  const pauseDisabled = readOnly || pause.isPending;
  const evaluateDisabled = readOnly || evaluate.isPending;

  const onConfirmAction = () => {
    if (confirm === "cleanup") {
      cleanup.mutate(undefined, {
        onSuccess: (r) => {
          toast.success(
            `Cleanup ran — deleted ${r.deleted ?? 0}, freed ${(r.freed_gb ?? 0).toFixed(1)} GB`,
          );
          setConfirm(null);
        },
        onError: (e) => toast.error(explain(e, "Cleanup failed")),
      });
      return;
    }
    if (confirm === "engage") {
      engage.mutate(undefined, {
        onSuccess: () => {
          toast.success("Lockdown engaged");
          setConfirm(null);
        },
        onError: (e) => toast.error(explain(e, "Engage failed")),
      });
      return;
    }
    if (confirm === "release") {
      release.mutate(undefined, {
        onSuccess: () => {
          toast.success("Lockdown released");
          setConfirm(null);
        },
        onError: (e) => toast.error(explain(e, "Release failed")),
      });
      return;
    }
  };

  const onEvaluate = () => {
    evaluate.mutate(undefined, {
      onSuccess: () => toast.success("Evaluation complete"),
      onError: (e) => toast.error(explain(e, "Evaluate failed")),
    });
  };

  const onPauseSubmit = () => {
    const n = Number(pauseHours);
    if (!Number.isFinite(n) || n < 1 || n > 24) {
      toast.error("Hours must be between 1 and 24");
      return;
    }
    pause.mutate(
      { hours: Math.round(n) },
      {
        onSuccess: () => {
          toast.success(`Auto evaluation paused for ${Math.round(n)}h`);
          setPauseOpen(false);
        },
        onError: (e) => toast.error(explain(e, "Pause failed")),
      },
    );
  };

  const buttons: ReadonlyArray<{
    key: string;
    testId: string;
    label: string;
    icon: typeof Trash2;
    variant: "primary" | "danger" | "secondary" | "default";
    disabled: boolean;
    onClick: () => void;
    loading: boolean;
  }> = [
    {
      key: "cleanup",
      testId: "storage-action-cleanup",
      label: "Run cleanup now",
      icon: Trash2,
      variant: "secondary",
      disabled: cleanupDisabled,
      onClick: () => setConfirm("cleanup"),
      loading: cleanup.isPending,
    },
    {
      key: "engage",
      testId: "storage-action-engage",
      label: "Engage lockdown",
      icon: Lock,
      variant: "danger",
      disabled: engageDisabled,
      onClick: () => setConfirm("engage"),
      loading: engage.isPending,
    },
    {
      key: "release",
      testId: "storage-action-release",
      label: "Release lockdown",
      icon: Play,
      variant: "primary",
      disabled: releaseDisabled,
      onClick: () => setConfirm("release"),
      loading: release.isPending,
    },
    {
      key: "pause",
      testId: "storage-action-pause",
      label: "Pause guardrails",
      icon: Pause,
      variant: "secondary",
      disabled: pauseDisabled,
      onClick: () => setPauseOpen(true),
      loading: pause.isPending,
    },
    {
      key: "evaluate",
      testId: "storage-action-evaluate",
      label: "Force evaluate",
      icon: Zap,
      variant: "secondary",
      disabled: evaluateDisabled,
      onClick: onEvaluate,
      loading: evaluate.isPending,
    },
  ];

  return (
    <div
      className="flex flex-wrap gap-2"
      data-testid="storage-action-buttons"
    >
      {buttons.map((b) => {
        const button = (
          <Button
            key={b.key}
            type="button"
            variant={b.variant}
            disabled={b.disabled}
            loading={b.loading}
            onClick={b.onClick}
            data-testid={b.testId}
          >
            <b.icon aria-hidden />
            {b.label}
          </Button>
        );
        if (readOnly) {
          return (
            <Tooltip key={b.key}>
              <TooltipTrigger asChild>
                <span>{button}</span>
              </TooltipTrigger>
              <TooltipContent
                data-testid={`${b.testId}-readonly-tooltip`}
              >
                {READ_ONLY_TOOLTIP}
              </TooltipContent>
            </Tooltip>
          );
        }
        return button;
      })}

      <Dialog
        open={confirm !== null}
        onOpenChange={(o) => {
          if (!o) setConfirm(null);
        }}
      >
        <DialogContent data-testid="storage-confirm-dialog">
          <DialogHeader>
            <DialogTitle>
              {confirm === "engage"
                ? "Engage manual lockdown?"
                : confirm === "release"
                  ? "Release lockdown?"
                  : "Run cleanup now?"}
            </DialogTitle>
            <DialogDescription>
              {confirm === "engage"
                ? "Pauses every download client. Trigger is recorded as MANUAL — auto-release at the Release threshold will not fire until you click Release."
                : confirm === "release"
                  ? "Resumes previously-paused download clients. If disk pressure is still over the Lockdown threshold, the AUTO loop may re-engage on the next tick."
                  : "Forces a synchronous cleanup pass regardless of disk percent. Deletes torrents matching the configured policy."}
            </DialogDescription>
          </DialogHeader>
          <div
            role="alert"
            className="flex items-start gap-2 rounded-md border border-[color-mix(in_oklab,var(--color-warning)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-warning)_10%,transparent)] p-3 text-xs text-warning"
          >
            <AlertTriangle aria-hidden className="mt-0.5 size-3.5 shrink-0" />
            <span>This action is logged in the audit feed.</span>
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="secondary">
                Cancel
              </Button>
            </DialogClose>
            <Button
              type="button"
              variant={confirm === "engage" ? "danger" : "primary"}
              onClick={onConfirmAction}
              loading={
                (confirm === "engage" && engage.isPending) ||
                (confirm === "release" && release.isPending) ||
                (confirm === "cleanup" && cleanup.isPending)
              }
              data-testid="storage-confirm-submit"
            >
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={pauseOpen} onOpenChange={setPauseOpen}>
        <DialogContent data-testid="storage-pause-dialog">
          <DialogHeader>
            <DialogTitle>Pause auto evaluation</DialogTitle>
            <DialogDescription>
              Stops the AUTO lockdown rule from firing for the chosen number
              of hours. Already-paused clients stay paused.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="storage-pause-hours">Hours</Label>
            <Select value={pauseHours} onValueChange={setPauseHours}>
              <SelectTrigger
                id="storage-pause-hours"
                data-testid="storage-pause-hours"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Array.from({ length: 24 }, (_, i) => i + 1).map((h) => (
                  <SelectItem key={h} value={String(h)}>
                    {h} hour{h === 1 ? "" : "s"}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="secondary">
                Cancel
              </Button>
            </DialogClose>
            <Button
              type="button"
              variant="primary"
              onClick={onPauseSubmit}
              loading={pause.isPending}
              data-testid="storage-pause-submit"
            >
              Pause
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
