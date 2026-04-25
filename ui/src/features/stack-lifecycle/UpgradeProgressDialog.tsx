import { useEffect, useRef } from "react";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { asArray } from "@/lib/coerce";
import { useStackUpgradeProgress } from "./hooks";

interface UpgradeProgressDialogProps {
  taskId: string;
  onClose: () => void;
}

const MAX_LOG_LINES = 50;

/**
 * Modal that polls `/api/stack/upgrade/{task_id}` every 5 s while the
 * task is running and renders a progress bar + tail log. The dialog is
 * non-dismissable while the task is `running`; once the server reports
 * `done` or `failed` the operator can close (and the polling stops on
 * its own — the hook returns `false` from `refetchInterval`).
 */
export function UpgradeProgressDialog({
  taskId,
  onClose,
}: UpgradeProgressDialogProps) {
  const progress = useStackUpgradeProgress(taskId);
  const data = progress.data;
  const state = data?.state ?? "queued";
  const running = state === "running";
  const done = state === "done";
  const failed = state === "failed";

  // Auto-scroll the tail log on every new frame so the operator always
  // sees the freshest line. The scroll only fires on actual updates so
  // a parent re-render at rest doesn't yank the cursor away.
  const logRef = useRef<HTMLPreElement | null>(null);
  const lines = asArray<string>(data?.log_tail);
  const tail = lines.slice(-MAX_LOG_LINES);
  const tailLength = tail.length;
  useEffect(() => {
    const el = logRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [tailLength]);

  const handleOpenChange = (next: boolean) => {
    // Disable close while running — Radix calls onOpenChange(false)
    // for both the close button and the escape/overlay click. We
    // veto every "false" request until terminal.
    if (!next && running) return;
    if (!next) onClose();
  };

  const fraction = clampFraction(data?.progress);
  const pct = Math.round(fraction * 100);

  return (
    <Dialog open onOpenChange={handleOpenChange}>
      <DialogContent
        data-testid="upgrade-progress-dialog"
        // While running, pointerDownOutside / escapeKeyDown must not
        // dismiss; we cancel the events so Radix doesn't propagate.
        onPointerDownOutside={(e) => {
          if (running) e.preventDefault();
        }}
        onEscapeKeyDown={(e) => {
          if (running) e.preventDefault();
        }}
        onInteractOutside={(e) => {
          if (running) e.preventDefault();
        }}
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {running ? (
              <Loader2
                className="size-5 animate-spin text-accent"
                aria-hidden
              />
            ) : done ? (
              <CheckCircle2 className="size-5 text-success" aria-hidden />
            ) : failed ? (
              <XCircle className="size-5 text-danger" aria-hidden />
            ) : (
              <Loader2 className="size-5 text-fg-muted" aria-hidden />
            )}
            <span data-testid="upgrade-progress-state">
              {running
                ? "Upgrade in progress"
                : done
                  ? "Upgrade complete"
                  : failed
                    ? "Upgrade failed"
                    : "Upgrade queued"}
            </span>
          </DialogTitle>
          <DialogDescription>
            Task {taskId} — {state}
            {typeof data?.progress === "number"
              ? ` · ${pct}%`
              : ""}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          <div
            className="h-2 overflow-hidden rounded-full bg-bg-2"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={pct}
            data-testid="upgrade-progress-bar"
          >
            <div
              className={
                failed
                  ? "h-full rounded-full bg-danger transition-[width] duration-200"
                  : done
                    ? "h-full rounded-full bg-success transition-[width] duration-200"
                    : "h-full rounded-full bg-accent transition-[width] duration-200"
              }
              style={{
                width: done ? "100%" : `${pct}%`,
              }}
            />
          </div>
        </div>

        <div className="space-y-1">
          <div className="text-xs font-medium text-fg-muted">Log tail</div>
          <pre
            ref={logRef}
            className="max-h-48 overflow-auto rounded-md border border-border bg-bg-2 p-3 font-mono text-xs leading-relaxed text-fg"
            data-testid="upgrade-progress-log"
          >
            {tail.length > 0
              ? tail.join("\n")
              : "(no output yet)"}
          </pre>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** Clamp `progress` (0..1 expected) to `[0, 1]`, defaulting to 0. */
function clampFraction(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return 0;
  if (value <= 0) return 0;
  if (value >= 1) return 1;
  return value;
}
