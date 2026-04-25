import { useCallback, useId, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { useHotkeys } from "react-hotkeys-hook";
import { toast } from "sonner";
import { ApiError, useReconcile } from "@/api";
import { Button } from "@/components/ui/button";
import { formatBytes } from "./format";

interface ReconcileButtonProps {
  disabled?: boolean;
}

/**
 * Primary-CTA button on the Media Integrity tab. Tucks a "Dry run"
 * checkbox alongside on sm+ viewports (hidden on phones for thumb
 * reach). Hotkey `r` fires the active mode whenever no input is
 * focused (react-hotkeys-hook's default scope filter handles that).
 */
export function ReconcileButton({ disabled = false }: ReconcileButtonProps) {
  const [dryRun, setDryRun] = useState(false);
  const reconcile = useReconcile();
  const reduce = useReducedMotion();
  const checkboxId = useId();

  const handleFire = useCallback(() => {
    if (disabled || reconcile.isPending) return;
    reconcile.mutate(
      { dryRun },
      {
        onSuccess: (detail) => {
          // Bytes-freed is best-effort; the report shape is opaque
          // until the OpenAPI codegen lands. Read it carefully off
          // the mutation result that React Query forwards into the
          // success callback (the `mutation.data` field is set on
          // the next tick, so reading it via `reconcile.data` here
          // would race the click and yield stale undefined).
          let bytes = 0;
          if (detail && typeof detail === "object") {
            const raw = (detail as unknown as Record<string, unknown>).bytes_freed;
            if (typeof raw === "number") bytes = raw;
          }
          const size = formatBytes(bytes);
          toast.success(
            dryRun
              ? `Dry-run preview — would free ${size}`
              : `Reconcile complete — freed ${size}`,
          );
        },
        onError: (err) => {
          const msg =
            err instanceof ApiError
              ? err.message
              : err instanceof Error
                ? err.message
                : "Reconcile failed";
          toast.error(msg);
        },
      },
    );
  }, [disabled, dryRun, reconcile]);

  useHotkeys("r", handleFire, { enabled: !disabled });

  return (
    <div className="flex items-center gap-3">
      <label
        htmlFor={checkboxId}
        className="hidden cursor-pointer select-none items-center gap-2 text-sm text-fg-muted sm:flex"
      >
        <input
          id={checkboxId}
          type="checkbox"
          checked={dryRun}
          onChange={(e) => setDryRun(e.target.checked)}
          className="size-4 rounded border-border-strong"
          data-testid="reconcile-dry-run"
        />
        Dry run
      </label>
      <Button
        variant="primary"
        disabled={disabled || reconcile.isPending}
        loading={reconcile.isPending}
        onClick={handleFire}
        data-testid="reconcile-button"
      >
        <AnimatePresence mode="wait" initial={false}>
          <motion.span
            key={dryRun ? "dry" : "real"}
            initial={reduce ? false : { opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduce ? { opacity: 0 } : { opacity: 0, y: -6 }}
            transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
          >
            {dryRun ? "Dry-run reconcile" : "Reconcile now"}
          </motion.span>
        </AnimatePresence>
      </Button>
    </div>
  );
}
