import { createRoute } from "@tanstack/react-router";
import { motion, useReducedMotion } from "framer-motion";
import { PageHeader } from "@/components/layout/PageHeader";
import { BackupRestoreCard } from "@/features/snapshots/BackupRestoreCard";
import { SnapshotsTable } from "@/features/snapshots/SnapshotsTable";
import { Route as RootRoute } from "@/routes/__root";

/**
 * Operational safety-net surface. Composes the snapshots list +
 * take/view/diff controls and the backup/restore card under a
 * shared PageHeader inside the standard route shell so the page
 * lines up width-for-width with every other tab.
 *
 * The route is intentionally not wired into `routeTree.ts` or the
 * Sidebar yet — the wave-3 merge step adds it from
 * `.ratchets/pending/wave3-routes/snapshots.txt` so concurrent
 * agents shipping sibling Operations features don't conflict.
 */
function SnapshotsRouteComponent() {
  const reduce = useReducedMotion();
  return (
    <motion.div
      className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6"
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
    >
      <PageHeader
        title="Snapshots"
        description="Configuration snapshots, diff, backup, and restore."
      />
      <SnapshotsTable />
      <BackupRestoreCard />
    </motion.div>
  );
}

export const SnapshotsRoute = createRoute({
  getParentRoute: () => RootRoute,
  path: "/snapshots",
  component: SnapshotsRouteComponent,
});

// Default export keeps the route file consistent with sibling
// feature routes (`audit-log.tsx`, `me.tsx`, ...) which expose `Route`.
export const Route = SnapshotsRoute;
