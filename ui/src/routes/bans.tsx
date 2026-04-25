import { createRoute } from "@tanstack/react-router";
import { motion, useReducedMotion } from "framer-motion";
import { PageHeader } from "@/components/layout/PageHeader";
import { Route as RootRoute } from "@/routes/__root";
import { BansPage } from "@/features/bans/BansPage";

/**
 * Bans tab — operator surface for blocking specific accounts and
 * IP/CIDR ranges. The two ban registries share a route but stay
 * stacked vertically because the rows aren't comparable.
 */
function BansRouteComponent() {
  const reduce = useReducedMotion();
  return (
    <motion.div
      className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6"
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
    >
      <PageHeader
        title="Bans"
        description="Block users and IP/CIDR ranges. Bans propagate to every provider."
      />
      <BansPage />
    </motion.div>
  );
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: "/bans",
  component: BansRouteComponent,
});
