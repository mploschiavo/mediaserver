import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import type { MediaIntegrityProgressShape } from "@/api";
import { formatRelative } from "./format";

interface ProgressBarProps {
  inFlight: boolean;
  progress?: MediaIntegrityProgressShape;
}

/**
 * Indeterminate progress strip for the Media Integrity tab.
 * Mounts only while `inFlight === true` (with a soft height-collapse
 * exit) and hosts a sliding-bar shimmer. Caption shows the active
 * op + phase + relative start time so operators can tell at a
 * glance whether a pass is fresh or stale.
 */
export function ProgressBar({ inFlight, progress }: ProgressBarProps) {
  const reduce = useReducedMotion();
  const active =
    progress && progress.in_progress
      ? (progress as Extract<MediaIntegrityProgressShape, { in_progress: true }>)
      : undefined;

  return (
    <AnimatePresence initial={false}>
      {inFlight ? (
        <motion.div
          key="mi-progress"
          initial={reduce ? false : { opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: "auto" }}
          exit={reduce ? { opacity: 0 } : { opacity: 0, height: 0 }}
          transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
          className="space-y-2 overflow-hidden"
          data-testid="mi-progress"
          role="status"
          aria-live="polite"
        >
          <div className="flex items-center justify-between text-xs text-fg-muted">
            <span>
              {active?.op === "reconcile"
                ? "Reconciling"
                : active?.op === "enforce_config"
                  ? "Enforcing config"
                  : "Working"}
              {active?.phase ? ` · ${active.phase}` : ""}
            </span>
            {active?.started_at ? (
              <span className="tabular-nums">
                started {formatRelative(active.started_at)}
              </span>
            ) : null}
          </div>
          <div
            className="h-1 overflow-hidden rounded-full bg-bg-2"
            aria-hidden
          >
            <motion.div
              className="h-full w-1/3 rounded-full bg-accent"
              animate={
                reduce ? { x: "0%" } : { x: ["-100%", "300%"] }
              }
              transition={
                reduce
                  ? { duration: 0 }
                  : { duration: 1.6, repeat: Infinity, ease: "linear" }
              }
            />
          </div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}
