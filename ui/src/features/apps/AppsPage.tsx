import { useMemo } from "react";
import { ExternalLink, Grid3x3, AppWindow } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/layout/EmptyState";
import { useServices, type ServiceEntry } from "./hooks";

const CATEGORY_ORDER: ReadonlyArray<{ key: string; label: string }> = [
  { key: "media", label: "Media servers" },
  { key: "automation", label: "Automation (*arr)" },
  { key: "downloads", label: "Download clients" },
  { key: "management", label: "Management" },
  { key: "infrastructure", label: "Infrastructure" },
];

const SERVICES_NOT_LAUNCHABLE = new Set([
  // Edge gateway and ext_authz — not user-facing apps.
  "envoy",
  "controller",
  // Headless services with no UI worth opening.
  "flaresolverr",
  "unpackerr",
]);

/**
 * /apps — grid of every deployed service that has a user-facing UI,
 * grouped by category. Each card opens the service at its
 * ``/app/<id>/`` mount point in a new tab.
 *
 * Reads the controller's service registry via ``GET /api/services``.
 * Hides services that don't surface a useful UI (Envoy, the
 * controller's own API, FlareSolverr proxy, Unpackerr daemon).
 */
export function AppsPage() {
  const query = useServices();
  const grouped = useMemo(() => groupByCategory(query.data?.services ?? []), [
    query.data,
  ]);

  if (query.isLoading) {
    return (
      <div className="flex flex-col gap-3" data-testid="apps-loading">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-24 w-full rounded-lg" />
        ))}
      </div>
    );
  }
  if (query.error) {
    return (
      <div
        role="alert"
        className="rounded-lg border border-[color-mix(in_oklab,var(--color-danger)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-danger)_10%,transparent)] p-4 text-sm text-danger"
        data-testid="apps-error"
      >
        <p className="font-medium">Failed to load services</p>
        <p className="mt-1 text-fg-muted">
          {(query.error as Error).message}
        </p>
      </div>
    );
  }
  const totalLaunchable = Object.values(grouped).reduce(
    (n, list) => n + list.length,
    0,
  );
  if (totalLaunchable === 0) {
    return (
      <div data-testid="apps-empty">
        <EmptyState
          icon={Grid3x3}
          title="No launchable apps"
          description="The controller hasn't reported any services with a user-facing UI yet. Check the bootstrap progress and the service registry."
        />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6" data-testid="apps-page">
      {CATEGORY_ORDER.map((cat) => {
        const list = grouped[cat.key] ?? [];
        if (list.length === 0) return null;
        return (
          <section key={cat.key} className="flex flex-col gap-3">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-fg-muted">
              {cat.label}
              <span className="ml-2 font-normal text-fg-faint">
                ({list.length})
              </span>
            </h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {list.map((s) => (
                <AppCard key={s.id} service={s} />
              ))}
            </div>
          </section>
        );
      })}
      {grouped.other && grouped.other.length > 0 ? (
        <section className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-fg-muted">
            Other
            <span className="ml-2 font-normal text-fg-faint">
              ({grouped.other.length})
            </span>
          </h2>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {grouped.other.map((s) => (
              <AppCard key={s.id} service={s} />
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}

function AppCard({ service }: { service: ServiceEntry }) {
  const href = serviceLaunchUrl(service.id);
  return (
    <Card data-testid={`apps-card-${service.id}`}>
      <CardHeader className="flex flex-row items-start justify-between gap-2 space-y-0">
        <div className="flex flex-col gap-1">
          <CardTitle className="flex items-center gap-2 text-base">
            <AppWindow aria-hidden className="size-4 text-fg-muted" />
            {service.name || service.id}
          </CardTitle>
          {service.desc ? (
            <CardDescription>{service.desc}</CardDescription>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="flex items-center justify-between gap-3">
        <code className="font-mono text-xs text-fg-muted">{href}</code>
        <Button
          asChild
          variant="primary"
          size="sm"
          data-testid={`apps-open-${service.id}`}
        >
          <a href={href} target="_blank" rel="noopener noreferrer">
            Open
            <ExternalLink aria-hidden className="size-3" />
          </a>
        </Button>
      </CardContent>
    </Card>
  );
}

function serviceLaunchUrl(id: string): string {
  // Sister apps mount under ``/app/<id>/`` on the same edge host.
  // The dashboard PWA's SW now passes these through (v1.3.52
  // navigateFallbackDenylist update), so a hard navigation lands on
  // the right service.
  return `/app/${id}/`;
}

function groupByCategory(
  services: readonly ServiceEntry[],
): Record<string, ServiceEntry[]> {
  const out: Record<string, ServiceEntry[]> = {};
  for (const s of services) {
    if (SERVICES_NOT_LAUNCHABLE.has(s.id)) continue;
    const key = s.category && CATEGORY_ORDER.some((c) => c.key === s.category)
      ? s.category
      : "other";
    (out[key] ??= []).push(s);
  }
  for (const list of Object.values(out)) {
    list.sort((a, b) => a.name.localeCompare(b.name));
  }
  return out;
}
