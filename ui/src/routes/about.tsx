import { createRoute } from "@tanstack/react-router";
import { motion, useReducedMotion } from "framer-motion";
import { PageHeader } from "@/components/layout/PageHeader";
import { AboutPage } from "@/features/about/AboutPage";
import { Route as RootRoute } from "@/routes/__root";

function AboutRouteComponent() {
  const reduce = useReducedMotion();
  return (
    <motion.div
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
    >
      <PageHeader
        title="About"
        description="Versions, source, and ways to support the project."
      />
      <AboutPage />
    </motion.div>
  );
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: "/about",
  component: AboutRouteComponent,
});
