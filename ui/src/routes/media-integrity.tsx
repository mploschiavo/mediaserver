import { createRoute } from "@tanstack/react-router";
import { motion, useReducedMotion } from "framer-motion";
import {
  useMediaIntegrityProgress,
  useMediaIntegrityStatus,
} from "@/api";
import { PageHeader } from "@/components/layout/PageHeader";
import { Route as RootRoute } from "@/routes/__root";
import {
  AdapterTable,
  EnforceButton,
  NeedsReviewPanel,
  ProgressBar,
  ReconcileButton,
  StatusOverview,
} from "@/features/media-integrity";

/**
 * Media Integrity tab — the luxury showcase route. Composes the
 * feature surface (status, adapters, needs-review queue) into one
 * mobile-first stack and surfaces the two operator CTAs in the
 * page header. Renders a quiet indeterminate progress strip while
 * a pass is running so reconcile/enforce can't fire concurrently.
 */
function MediaIntegrityPage() {
  const reduce = useReducedMotion();
  const status = useMediaIntegrityStatus();
  const progress = useMediaIntegrityProgress();
  const inFlight = progress.data?.in_progress === true;

  return (
    <motion.div
      className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6"
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
    >
      <PageHeader
        title="Media Integrity"
        description="Anti-duplicate engine across Radarr, Sonarr, Lidarr, Readarr, and Bazarr."
        actions={
          <div className="flex gap-2">
            <EnforceButton disabled={inFlight} />
            <ReconcileButton disabled={inFlight} />
          </div>
        }
      />
      <ProgressBar inFlight={inFlight} progress={progress.data} />
      <StatusOverview
        status={status.data}
        loading={status.isLoading}
        error={status.error}
      />
      <AdapterTable status={status.data} loading={status.isLoading} />
      <NeedsReviewPanel status={status.data} />
    </motion.div>
  );
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: "/media-integrity",
  component: MediaIntegrityPage,
});
