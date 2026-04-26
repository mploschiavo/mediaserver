import { useMemo } from "react";
import { sankey, sankeyLeft, sankeyLinkHorizontal } from "d3-sankey";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useEnvoyAdminSummary } from "./useEnvoyAdminSummary";

/**
 * Live Sankey flow diagram: Gateway → top-N upstream clusters,
 * widths proportional to current request_totals. Anything outside
 * the top 8 clusters collapses into "other" so a 50-service deploy
 * stays readable. Colours are tone-tinted from the same palette as
 * the per-cluster traffic chart so a single cluster looks the same
 * across every visualisation on the page.
 *
 * Why a Sankey vs a pie: pies are good for static distribution; a
 * Sankey hints at *flow* (left → right reads as "where does traffic
 * go"). For multi-hop networks (HTTP listener → ext_authz →
 * cluster) it'd be even more compelling — extending to that shape
 * is straightforward once the controller exposes per-listener stats.
 */
export function SankeyFlowCard() {
  const q = useEnvoyAdminSummary();
  const data = q.data;

  const layout = useMemo(() => {
    if (!data) return null;
    const totals = Object.entries(data.request_totals)
      .filter(([, n]) => n > 0)
      .sort(([, a], [, b]) => b - a);
    if (totals.length === 0) return null;

    const top = totals.slice(0, 8);
    const restSum = totals.slice(8).reduce((a, [, v]) => a + v, 0);

    // Two-column Sankey: source = "Gateway", sinks = clusters.
    // d3-sankey requires a node array + link array indexed by node
    // position (or by `name` when supplied with `nodeId`).
    type N = { id: string; label: string; kind: "gateway" | "cluster" };
    type L = { source: string; target: string; value: number };
    const nodes: N[] = [
      { id: "__gateway__", label: data.gateway_label || "Gateway", kind: "gateway" },
      ...top.map(([cluster]): N => ({
        id: cluster,
        label: prettyCluster(cluster),
        kind: "cluster",
      })),
    ];
    const links: L[] = top.map(([cluster, value]) => ({
      source: "__gateway__",
      target: cluster,
      value,
    }));
    if (restSum > 0) {
      nodes.push({ id: "__other__", label: "other", kind: "cluster" });
      links.push({ source: "__gateway__", target: "__other__", value: restSum });
    }

    const WIDTH = 720;
    const HEIGHT = 320;
    const PAD = 16;
    const sankeyGen = sankey<N, L>()
      .nodeId((n) => n.id)
      .nodeAlign(sankeyLeft)
      .nodeWidth(14)
      .nodePadding(8)
      .extent([
        [PAD, PAD],
        [WIDTH - PAD, HEIGHT - PAD],
      ]);
    const graph = sankeyGen({
      nodes: nodes.map((n) => ({ ...n })),
      links: links.map((l) => ({ ...l })),
    });
    return { graph, WIDTH, HEIGHT };
  }, [data]);

  if (q.isLoading) {
    return (
      <Card data-testid="sankey-flow-card-loading">
        <CardHeader>
          <CardTitle>Traffic flow</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-64 w-full rounded-md" />
        </CardContent>
      </Card>
    );
  }

  if (q.error || !data) {
    return null; // sibling cards already surface the admin-summary error
  }

  return (
    <Card data-testid="sankey-flow-card">
      <CardHeader>
        <CardTitle>Traffic flow</CardTitle>
        <CardDescription>
          Live request-volume flow from the gateway to upstream
          clusters. Widths are proportional to current{" "}
          <code className="text-fg">request_totals</code>; top 8
          clusters get their own ribbon, anything quieter rolls into
          "other".
        </CardDescription>
      </CardHeader>
      <CardContent>
        {!layout ? (
          <div
            className="flex h-32 w-full items-center justify-center rounded border border-dashed border-border/60 bg-bg-1/40 text-xs text-fg-muted"
            data-testid="sankey-flow-empty"
          >
            No traffic recorded yet — the diagram will appear once a
            cluster has handled a request.
          </div>
        ) : (
          <div className="w-full overflow-x-auto">
            <svg
              viewBox={`0 0 ${layout.WIDTH} ${layout.HEIGHT}`}
              className="h-64 w-full"
              role="img"
              aria-label="Sankey diagram of gateway-to-cluster traffic flow"
              data-testid="sankey-flow-svg"
            >
              {/* Links first so node rectangles paint over the
                  ribbons' joints. */}
              <g fill="none" strokeOpacity={0.4}>
                {layout.graph.links.map((link, i) => {
                  const path = sankeyLinkHorizontal()(link as never);
                  if (!path) return null;
                  const targetId = (link.target as { id: string }).id;
                  const tone = clusterTone(i, targetId);
                  return (
                    <Tooltip key={i}>
                      <TooltipTrigger asChild>
                        <path
                          d={path}
                          stroke={tone}
                          strokeWidth={Math.max(1, link.width ?? 1)}
                          data-testid={`sankey-link-${targetId}`}
                        />
                      </TooltipTrigger>
                      <TooltipContent>
                        {prettyCluster(targetId)} ·{" "}
                        {link.value.toLocaleString()} req
                      </TooltipContent>
                    </Tooltip>
                  );
                })}
              </g>
              {/* Nodes */}
              <g>
                {layout.graph.nodes.map((node, i) => {
                  const x0 = node.x0 ?? 0;
                  const x1 = node.x1 ?? 0;
                  const y0 = node.y0 ?? 0;
                  const y1 = node.y1 ?? 0;
                  const isGateway = node.kind === "gateway";
                  const fill = isGateway
                    ? "var(--color-accent)"
                    : clusterTone(i, node.id);
                  return (
                    <g
                      key={node.id}
                      data-testid={`sankey-node-${node.id}`}
                    >
                      <rect
                        x={x0}
                        y={y0}
                        width={x1 - x0}
                        height={y1 - y0}
                        fill={fill}
                        rx={2}
                      />
                      <text
                        x={isGateway ? x1 + 6 : x0 - 6}
                        y={(y0 + y1) / 2}
                        dy="0.35em"
                        textAnchor={isGateway ? "start" : "end"}
                        className="fill-[var(--color-fg)]"
                        fontSize={11}
                      >
                        {node.label}
                      </text>
                    </g>
                  );
                })}
              </g>
            </svg>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

const SANKEY_PALETTE = [
  "var(--color-info)",
  "var(--color-success)",
  "var(--color-warning)",
  "var(--color-accent)",
  "var(--color-danger)",
  "var(--color-fg-muted)",
  "var(--color-info)",
  "var(--color-success)",
  "var(--color-fg-faint)",
];

function clusterTone(i: number, id: string): string {
  if (id === "__other__") return "var(--color-fg-faint)";
  return SANKEY_PALETTE[i % SANKEY_PALETTE.length] ?? "var(--color-fg-muted)";
}

function prettyCluster(name: string): string {
  if (name === "__gateway__") return "Gateway";
  if (name === "__other__") return "other";
  if (name.startsWith("service_")) return name.slice("service_".length);
  return name;
}
