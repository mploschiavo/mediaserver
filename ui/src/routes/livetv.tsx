import { createRoute } from "@tanstack/react-router";
import { motion, useReducedMotion } from "framer-motion";
import { PageHeader } from "@/components/layout/PageHeader";
import { LivetvPage } from "@/features/livetv/LivetvPage";
import { Route as RootRoute } from "@/routes/__root";

/**
 * Operator-facing Live TV / IPTV / EPG configuration route. Mounts
 * the `<LivetvPage />` composition (sources + IPTV countries + EPG
 * providers + EPG health) inside the standard route shell so the
 * page lines up width-for-width with every other tab.
 *
 * The route is intentionally not wired into `routeTree.ts` or the
 * Sidebar yet — the wave-5 merge step adds it from
 * `.ratchets/pending/wave5-routes/livetv.txt` so concurrent sibling
 * agents shipping other Library features don't conflict.
 */
function LivetvRouteComponent() {
  const reduce = useReducedMotion();
  return (
    <motion.div
      className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6"
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
    >
      <PageHeader
        title="Live TV"
        description="Configure M3U / EPG sources Jellyfin uses for live TV and DVR."
      />
      <LivetvPage />
    </motion.div>
  );
}

export const LivetvRoute = createRoute({
  getParentRoute: () => RootRoute,
  path: "/livetv",
  component: LivetvRouteComponent,
});

// Default export keeps the route file consistent with sibling
// feature routes (`auth.tsx`, `me.tsx`, ...) which expose `Route`.
export const Route = LivetvRoute;
