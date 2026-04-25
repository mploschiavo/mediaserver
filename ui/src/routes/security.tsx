import { createRoute } from "@tanstack/react-router";
import { motion, useReducedMotion } from "framer-motion";
import { PageHeader } from "@/components/layout/PageHeader";
import { Route as RootRoute } from "@/routes/__root";
import { SecurityPage } from "@/features/security-signals/SecurityPage";

/**
 * Security signals tab — abuse-defence read surface composed of
 * failed-login clusters, new-location alerts, and concurrent-session
 * spikes. The route owns the outer `max-w-6xl` page-shell + PageHeader
 * so every tab lines up width-for-width; data fetching lives inside
 * the feature folder.
 */
function SecurityRouteComponent() {
  const reduce = useReducedMotion();
  return (
    <motion.div
      className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6"
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
    >
      <PageHeader
        title="Security signals"
        description="Failed logins, new-location alerts, concurrent-session spikes."
      />
      <SecurityPage />
    </motion.div>
  );
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: "/security",
  component: SecurityRouteComponent,
});
