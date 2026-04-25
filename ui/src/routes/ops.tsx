import { createRoute, Link } from "@tanstack/react-router";
import { motion, useReducedMotion } from "framer-motion";
import {
  ActivitySquare,
  KeyRound,
  PackageSearch,
  RefreshCw,
  ScrollText,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { toast } from "sonner";
import { ApiError, useOpsAction, useOpsHealth } from "@/api";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { GpuCard } from "@/features/infra-detail/GpuCard";
import { ImageUpdatesCard } from "@/features/infra-detail/ImageUpdatesCard";
import { MountsCard } from "@/features/infra-detail/MountsCard";
import { StorageBreakdownCard } from "@/features/infra-detail/StorageBreakdownCard";
import { ConfigIntegrityCard } from "@/features/ops-detail/ConfigIntegrityCard";
import { CrashloopsCard } from "@/features/ops-detail/CrashloopsCard";
import { FailedServicesCard } from "@/features/ops-detail/FailedServicesCard";
import { HealthHistorySparkline } from "@/features/ops-detail/HealthHistorySparkline";
import { HealthStoriesCard } from "@/features/ops-detail/HealthStoriesCard";
import { Route as RootRoute } from "@/routes/__root";

type ActionKey = "refreshServices" | "rotateKeys" | "pullManifests" | "healthProbe";

interface ActionButton {
  key: ActionKey | "navigate";
  label: string;
  icon: typeof RefreshCw;
  to?: string;
  action?: ActionKey;
  successLabel?: string;
}

const ACTIONS: readonly ActionButton[] = [
  { key: "refreshServices", action: "refreshServices", label: "Refresh services", icon: RefreshCw, successLabel: "Services refreshed" },
  { key: "rotateKeys", action: "rotateKeys", label: "Rotate API keys", icon: KeyRound, successLabel: "API keys rotated" },
  { key: "pullManifests", action: "pullManifests", label: "Re-pull manifests", icon: PackageSearch, successLabel: "Manifests pulled" },
  { key: "navigate", to: "/media-integrity", label: "Clean up duplicates", icon: Sparkles },
  { key: "healthProbe", action: "healthProbe", label: "Run health probe", icon: ShieldCheck, successLabel: "Health probe queued" },
  { key: "navigate", to: "/logs", label: "Show diagnostics", icon: ScrollText },
];

interface ActionTileProps {
  spec: ActionButton;
}

function ActionTile({ spec }: ActionTileProps) {
  // Hooks must run unconditionally; we always create a mutation,
  // but only invoke it when the spec carries an action.
  const fallbackKey: ActionKey = "refreshServices";
  const mutate = useOpsAction(spec.action ?? fallbackKey);

  if (spec.key === "navigate" && spec.to) {
    return (
      <Button asChild variant="secondary" className="h-full justify-start">
        <Link to={spec.to} data-testid={`ops-link-${spec.to}`}>
          <spec.icon aria-hidden />
          <span>{spec.label}</span>
        </Link>
      </Button>
    );
  }

  const handle = () => {
    mutate.mutate(undefined, {
      onSuccess: () => toast.success(spec.successLabel ?? "Done"),
      onError: (err) => {
        const msg =
          err instanceof ApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : "Action failed";
        toast.error(msg);
      },
    });
  };

  return (
    <Button
      variant="secondary"
      className="h-full justify-start"
      onClick={handle}
      loading={mutate.isPending}
      data-testid={`ops-action-${spec.action}`}
    >
      <spec.icon aria-hidden />
      <span>{spec.label}</span>
    </Button>
  );
}

function formatUptime(seconds: number): string {
  if (seconds <= 0) return "—";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  if (d > 0) return `${d}d ${h}h`;
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

function OpsPage() {
  const reduce = useReducedMotion();
  const health = useOpsHealth();

  return (
    <motion.div
      className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6"
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
    >
      <PageHeader
        title="Operations"
        description="System-level actions and health."
      />

      <div
        className="grid grid-cols-2 gap-3 sm:grid-cols-3"
        data-testid="ops-action-grid"
      >
        {ACTIONS.map((spec, i) => (
          <ActionTile key={`${spec.key}-${i}`} spec={spec} />
        ))}
      </div>

      <Card data-testid="ops-health">
        <CardHeader>
          <CardTitle>Health</CardTitle>
          <CardDescription>aggregated runtime stats</CardDescription>
        </CardHeader>
        <CardContent>
          {health.isLoading ? (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              {[0, 1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : health.error ? (
            <div role="alert" data-testid="ops-health-error" className="text-sm text-danger">
              {health.error.message}
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 text-sm">
              <div>
                <div className="text-fg-muted">Uptime</div>
                <div className="mt-1 font-mono tabular-nums text-fg">
                  {formatUptime(health.data?.uptime_seconds ?? 0)}
                </div>
              </div>
              <div>
                <div className="text-fg-muted">Containers</div>
                <div className="mt-1 font-mono tabular-nums text-fg">
                  {health.data?.containers ?? 0}
                </div>
              </div>
              <div>
                <div className="text-fg-muted">Disk used</div>
                <div className="mt-1 font-mono tabular-nums text-fg">
                  {(health.data?.disk_used_pct ?? 0).toFixed(1)}%
                </div>
              </div>
              <div>
                <div className="text-fg-muted flex items-center gap-1">
                  <ActivitySquare aria-hidden className="size-3" />
                  Last bootstrap
                </div>
                <div className="mt-1 font-mono tabular-nums text-fg">
                  {health.data?.last_bootstrap_at
                    ? new Date(health.data.last_bootstrap_at).toLocaleString()
                    : "—"}
                </div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <section
        aria-label="Detailed health surface"
        className="grid grid-cols-1 gap-4 md:grid-cols-2"
        data-testid="ops-detail-grid"
      >
        <HealthStoriesCard />
        <CrashloopsCard />
        <FailedServicesCard />
        <ConfigIntegrityCard />
        <HealthHistorySparkline />
      </section>

      <section
        aria-label="Infrastructure detail"
        className="grid grid-cols-1 gap-4 md:grid-cols-2"
        data-testid="infra-detail-grid"
      >
        <GpuCard />
        <MountsCard />
        <StorageBreakdownCard />
        <ImageUpdatesCard />
      </section>
    </motion.div>
  );
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: "/ops",
  component: OpsPage,
});
