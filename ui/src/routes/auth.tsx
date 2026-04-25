import { createRoute } from "@tanstack/react-router";
import { motion, useReducedMotion } from "framer-motion";
import { PageHeader } from "@/components/layout/PageHeader";
import { AuthAdminPage } from "@/features/auth-admin/AuthAdminPage";
import { Route as RootRoute } from "@/routes/__root";

/**
 * Operator-facing auth configuration route. Mounts the
 * `<AuthAdminPage />` composition (auth mode + OIDC providers +
 * per-service policies) inside the standard route shell so the page
 * lines up width-for-width with every other tab.
 *
 * The route is intentionally not wired into `routeTree.ts` or the
 * Sidebar yet — the wave-4 merge step adds it from
 * `.ratchets/pending/wave4-routes/auth-admin.txt` so the concurrent
 * agents shipping sibling Identity features don't conflict.
 */
function AuthRouteComponent() {
  const reduce = useReducedMotion();
  return (
    <motion.div
      className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6"
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
    >
      <PageHeader
        title="Auth"
        description="Pick the global auth strategy, federate identity providers, and gate per-service access."
      />
      <AuthAdminPage />
    </motion.div>
  );
}

export const AuthAdminRoute = createRoute({
  getParentRoute: () => RootRoute,
  path: "/auth",
  component: AuthRouteComponent,
});

// Default export keeps the route file consistent with sibling
// feature routes (`audit-log.tsx`, `me.tsx`, ...) which expose `Route`.
export const Route = AuthAdminRoute;
