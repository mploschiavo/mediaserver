import { createRoute } from "@tanstack/react-router";
import { motion, useReducedMotion } from "framer-motion";
import { PageHeader } from "@/components/layout/PageHeader";
import { SessionsByProviderChart } from "@/features/sessions/SessionsByProviderChart";
import { SessionsTable } from "@/features/sessions/SessionsTable";
import { Route as RootRoute } from "@/routes/__root";

/**
 * Active-sessions tab — operator surface for revoking live sessions
 * across every provider (controller, Authelia, Jellyfin, Jellyseerr,
 * native admin). Mounts the `SessionsTable` feature inside the
 * standard page chrome.
 */
function SessionsPage() {
  const reduce = useReducedMotion();
  return (
    <motion.div
      className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6"
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
    >
      <PageHeader
        title="Active sessions"
        description="Sessions across every provider. Revoke any that look wrong."
      />
      <SessionsByProviderChart />
      <SessionsTable />
    </motion.div>
  );
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: "/sessions",
  component: SessionsPage,
});

export const SessionsRoute = Route;
