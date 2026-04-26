import { createRoute } from "@tanstack/react-router";
import { motion, useReducedMotion } from "framer-motion";
import { PageHeader } from "@/components/layout/PageHeader";
import { DnsCheckCard } from "@/features/routing-admin/DnsCheckCard";
import { EnvoyAdminSummaryCard } from "@/features/routing-admin/EnvoyAdminSummaryCard";
import { GatewayHostnamesCard } from "@/features/routing-admin/GatewayHostnamesCard";
import { ReachabilityMatrix } from "@/features/routing-admin/ReachabilityMatrix";
import { RoutingStrategyCard } from "@/features/routing-admin/RoutingStrategyCard";
import { TlsCertificateCard } from "@/features/routing-admin/TlsCertificateCard";
import { useRouting } from "@/features/routing-admin/hooks";
import { Route as RootRoute } from "@/routes/__root";

/**
 * Routing operator surface, recomposed from the wave-3 feature
 * components. Order is deliberate: strategy + apps first (the most
 * common context check), then the diagnostic surfaces (reachability →
 * DNS → gateway hostnames), then the TLS install panel at the bottom
 * — destructive cert operations stay below-the-fold so they don't
 * become the first thing an operator clicks.
 */
function RoutingPage() {
  const reduce = useReducedMotion();
  const routing = useRouting();

  return (
    <motion.div
      className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6"
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
    >
      <PageHeader
        title="Routing"
        description="Strategy + reachability + TLS for the controller's edge."
      />

      <RoutingStrategyCard
        loading={routing.isLoading}
        data={routing.data}
        error={routing.error}
        onRetry={() => void routing.refetch()}
      />

      <EnvoyAdminSummaryCard />

      <ReachabilityMatrix />

      <DnsCheckCard />

      <GatewayHostnamesCard />

      <TlsCertificateCard />
    </motion.div>
  );
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: "/routing",
  component: RoutingPage,
});
