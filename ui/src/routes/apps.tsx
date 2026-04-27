import { createRoute } from "@tanstack/react-router";
import { motion, useReducedMotion } from "framer-motion";
import { PageHeader } from "@/components/layout/PageHeader";
import { AppsPage } from "@/features/apps/AppsPage";
import { Route as RootRoute } from "@/routes/__root";

function AppsRouteComponent() {
  const reduce = useReducedMotion();
  return (
    <motion.div
      className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6"
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
    >
      <PageHeader
        title="Apps"
        description="Open any deployed service in a new tab. Sister apps live behind the same edge gateway as this dashboard."
      />
      <AppsPage />
    </motion.div>
  );
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: "/apps",
  component: AppsRouteComponent,
});
