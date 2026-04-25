import { createRoute } from "@tanstack/react-router";
import { motion, useReducedMotion } from "framer-motion";
import { PageHeader } from "@/components/layout/PageHeader";
import { JobsPage } from "@/features/jobs/JobsPage";
import { Route as RootRoute } from "@/routes/__root";

/**
 * `/jobs` route — operator surface for the controller's job graph.
 * Renders a two-pane layout: a recursive hierarchy tree on the left
 * and a per-job detail panel (or batch-history fallback) on the
 * right. The route owns the outer `max-w-6xl` page-shell + PageHeader
 * so every tab lines up width-for-width; the actual page composition
 * lives next to its hooks in `src/features/jobs/`.
 */
function JobsRouteComponent() {
  const reduce = useReducedMotion();
  return (
    <motion.div
      className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6"
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
      data-testid="jobs-page"
    >
      <PageHeader
        title="Jobs"
        description="Hierarchy, run history, and per-action triggers. Polls every 5 seconds."
      />
      <JobsPage />
    </motion.div>
  );
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: "/jobs",
  component: JobsRouteComponent,
});
