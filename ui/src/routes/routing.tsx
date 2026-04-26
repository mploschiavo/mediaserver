import { createRoute } from "@tanstack/react-router";
import { motion, useReducedMotion } from "framer-motion";
import { PageHeader } from "@/components/layout/PageHeader";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApexCatchAllCard } from "@/features/routing-admin/ApexCatchAllCard";
import { DefaultsCard } from "@/features/routing-admin/DefaultsCard";
import { DnsCheckCard } from "@/features/routing-admin/DnsCheckCard";
import { EnvoyAdminSummaryCard } from "@/features/routing-admin/EnvoyAdminSummaryCard";
import { ExposureCard } from "@/features/routing-admin/ExposureCard";
import { GatewayHostnamesCard } from "@/features/routing-admin/GatewayHostnamesCard";
import { HostnamesMatrix } from "@/features/routing-admin/HostnamesMatrix";
import { PathAliasesCard } from "@/features/routing-admin/PathAliasesCard";
import { ReachabilityMatrix } from "@/features/routing-admin/ReachabilityMatrix";
import { RouteTableCard } from "@/features/routing-admin/RouteTableCard";
import { RoutingStrategyCard } from "@/features/routing-admin/RoutingStrategyCard";
import { TlsCertificateCard } from "@/features/routing-admin/TlsCertificateCard";
import { useRouting } from "@/features/routing-admin/hooks";
import { Route as RootRoute } from "@/routes/__root";

/**
 * Edge gateway operator surface, organised in three tabs (Cloudflare /
 * AWS Cloudfront-style):
 *
 *   * **Config** — the editable + visible routing rules: strategy,
 *     exposure, hostnames, path aliases, apex/catch-all, defaults,
 *     TLS, *and the live route table* (the "where does /app/jellyfin
 *     go?" answer that was missing from earlier PRs).
 *   * **Live** — Envoy admin observability: KPIs, sparklines, pies,
 *     latency heatmap, request rate.
 *   * **Diagnostics** — reachability matrix, DNS probe, gateway
 *     hostname inventory.
 *
 * Splitting reduces page sprawl (six cards on /routing was the
 * complaint) and makes each surface answer one question. URLs use
 * search params so a tab is shareable.
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
        title="Edge Gateway"
        description="Routing config, live traffic, and diagnostics for the controller's edge."
      />

      <Tabs defaultValue="config">
        <TabsList>
          <TabsTrigger value="config">Config</TabsTrigger>
          <TabsTrigger value="live">Live</TabsTrigger>
          <TabsTrigger value="diagnostics">Diagnostics</TabsTrigger>
        </TabsList>

        <TabsContent value="config" className="flex flex-col gap-6 pt-4">
          <RoutingStrategyCard
            loading={routing.isLoading}
            data={routing.data}
            error={routing.error}
            onRetry={() => void routing.refetch()}
          />
          <ExposureCard />
          <HostnamesMatrix />
          <RouteTableCard />
          <PathAliasesCard />
          <ApexCatchAllCard />
          <DefaultsCard />
          <TlsCertificateCard />
        </TabsContent>

        <TabsContent value="live" className="flex flex-col gap-6 pt-4">
          <EnvoyAdminSummaryCard />
        </TabsContent>

        <TabsContent value="diagnostics" className="flex flex-col gap-6 pt-4">
          <ReachabilityMatrix />
          <DnsCheckCard />
          <GatewayHostnamesCard />
        </TabsContent>
      </Tabs>
    </motion.div>
  );
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: "/routing",
  component: RoutingPage,
});
