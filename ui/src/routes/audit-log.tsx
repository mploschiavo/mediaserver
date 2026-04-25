import { createRoute } from "@tanstack/react-router";
import { motion, useReducedMotion } from "framer-motion";
import { PageHeader } from "@/components/layout/PageHeader";
import { AuditLogPage } from "@/features/audit-log/AuditLogPage";
import { Route as RootRoute } from "@/routes/__root";

/**
 * Search-param shape for the /audit-log route. The single optional
 * `action` key matches the existing prefix filter on the audit-log
 * list endpoint, so deep-links from sibling features can pre-fill
 * the filter input (e.g. `/audit-log?action=job:reconcile`).
 */
export interface AuditLogSearch {
  action?: string;
}

/**
 * Tamper-evident audit-log route. Composes the feature surface
 * (integrity banner + filterable table) under a shared PageHeader
 * inside the standard route shell so the page lines up width-for-
 * width with every other tab.
 *
 * This route is intentionally not wired into `routeTree.ts` or
 * the Sidebar yet — the wave-2 merge step adds it from
 * `.ratchets/pending/wave2-routes/audit-log.txt` so concurrent
 * agents shipping sibling observability features don't conflict.
 */
function AuditLogRouteComponent() {
  const reduce = useReducedMotion();
  return (
    <motion.div
      className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6"
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
    >
      <PageHeader
        title="Audit log"
        description="Tamper-evident record of every operator action."
      />
      <AuditLogPage />
    </motion.div>
  );
}

export const AuditLogRoute = createRoute({
  getParentRoute: () => RootRoute,
  path: "/audit-log",
  component: AuditLogRouteComponent,
  validateSearch: (raw: Record<string, unknown>): AuditLogSearch => {
    const out: AuditLogSearch = {};
    const action = raw.action;
    if (typeof action === "string" && action.length > 0) {
      out.action = action;
    }
    return out;
  },
});

// Default export keeps the route file consistent with sibling
// feature routes (`me.tsx`, `users.tsx`, ...) which expose `Route`.
export const Route = AuditLogRoute;
