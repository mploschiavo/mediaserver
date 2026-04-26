import { useEffect, useMemo, useRef, useState } from "react";
import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
} from "d3-force";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useEnvoyAdminSummary } from "./useEnvoyAdminSummary";

/**
 * Service map / topology graph — d3-force layout of:
 *
 *   * Source node ("Internet") on the left
 *   * Gateway node (Envoy) in the middle, sized by total active
 *     connections
 *   * One node per upstream cluster on the right, sized by current
 *     request volume, colour-graded by health (green = all hosts
 *     healthy, amber = degraded, red = no healthy hosts)
 *   * Edges between Internet → Envoy and Envoy → each cluster.
 *     Width proportional to request rate. Animated dashes flow
 *     along edges with active traffic so the graph "feels alive"
 *     without burning CPU on real animation frames.
 *
 * Click any cluster node → drilldown drawer (same one used by the
 * other cards on the page) — but since this card lives at a
 * higher level than the existing drawer, we use a window event
 * to ask the parent EnvoyAdminSummaryCard to open it. (Trade-off:
 * tight coupling vs full-page event bus; acceptable since both
 * cards live on the same page.)
 *
 * Layout: simulation runs for ~200 ticks at mount (then frozen)
 * so we don't hammer the CPU on every poll. The graph re-runs
 * the simulation only when the cluster set changes (added /
 * removed services), not on every traffic delta.
 */
export function TopologyGraphCard() {
  const q = useEnvoyAdminSummary();
  const data = q.data;

  const svgRef = useRef<SVGSVGElement>(null);

  // Build the graph from the admin summary.
  const graph = useMemo(() => {
    if (!data) return null;
    const totals = Object.entries(data.request_totals).filter(([, n]) => n > 0);
    if (totals.length === 0) return null;
    type Node = {
      id: string;
      label: string;
      kind: "internet" | "gateway" | "cluster";
      value: number;
      healthy?: number;
      hosts?: number;
      x?: number;
      y?: number;
      fx?: number | null;
      fy?: number | null;
    };
    type Link = { source: string; target: string; value: number };

    const totalReq = totals.reduce((a, [, v]) => a + v, 0);
    const totalActive = Object.values(data.active_connections).reduce(
      (a, b) => a + b,
      0,
    );

    // Map cluster name → health row.
    const clusterHealth = new Map<string, { healthy: number; hosts: number }>();
    for (const c of data.clusters) {
      clusterHealth.set(c.name, { healthy: c.healthy, hosts: c.hosts });
    }

    const nodes: Node[] = [
      {
        id: "__internet__",
        label: "Internet",
        kind: "internet",
        value: totalActive,
        // Pin the source node on the left so the layout is left → right.
        fx: 60,
      },
      {
        id: "__gateway__",
        label: "Envoy",
        kind: "gateway",
        value: totalReq,
      },
      ...totals
        .sort(([, a], [, b]) => b - a)
        .slice(0, 12)
        .map(([name, value]): Node => ({
          id: name,
          label: prettyCluster(name),
          kind: "cluster",
          value,
          healthy: clusterHealth.get(name)?.healthy,
          hosts: clusterHealth.get(name)?.hosts,
        })),
    ];
    const links: Link[] = [
      { source: "__internet__", target: "__gateway__", value: totalActive || 1 },
      ...nodes
        .filter((n) => n.kind === "cluster")
        .map(
          (n): Link => ({
            source: "__gateway__",
            target: n.id,
            value: n.value,
          }),
        ),
    ];
    return { nodes, links, totalReq, totalActive };
  }, [data]);

  const [layout, setLayout] = useState<{
    nodes: ReadonlyArray<{
      id: string;
      x: number;
      y: number;
      kind: "internet" | "gateway" | "cluster";
      label: string;
      value: number;
      healthy?: number;
      hosts?: number;
    }>;
    links: ReadonlyArray<{ sourceId: string; targetId: string; sx: number; sy: number; tx: number; ty: number; value: number; }>;
  } | null>(null);

  const WIDTH = 760;
  const HEIGHT = 420;

  // Re-run simulation only when the *set* of clusters changes —
  // not on every traffic delta. Otherwise the graph would jiggle
  // on every poll and operators couldn't read it.
  const clusterSetKey = useMemo(
    () =>
      graph?.nodes
        .filter((n) => n.kind === "cluster")
        .map((n) => n.id)
        .sort()
        .join(",") ?? "",
    [graph],
  );

  useEffect(() => {
    if (!graph) {
      setLayout(null);
      return;
    }
    // Pin internet on the left and gateway in the middle so we get
    // a stable left → right flow.
    const simNodes = graph.nodes.map((n) => ({
      ...n,
      fx: n.id === "__internet__" ? 60 : n.id === "__gateway__" ? WIDTH / 2 : null,
      fy: n.id === "__internet__" ? HEIGHT / 2 : n.id === "__gateway__" ? HEIGHT / 2 : null,
      x: n.id === "__internet__" ? 60 : n.id === "__gateway__" ? WIDTH / 2 : WIDTH - 80,
      y: HEIGHT / 2,
    }));
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const sim = forceSimulation(simNodes as any)
      .force(
        "link",
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        forceLink(graph.links.map((l) => ({ ...l }))).id((d: any) => d.id).distance(120).strength(0.3),
      )
      .force("charge", forceManyBody().strength(-200))
      .force("collide", forceCollide(28))
      .force("center", forceCenter(WIDTH / 2, HEIGHT / 2))
      .force(
        "x-spread",
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        forceX((d: any) => (d.kind === "cluster" ? WIDTH - 100 : WIDTH / 2)).strength(0.1),
      )
      .force("y-spread", forceY(HEIGHT / 2).strength(0.05))
      .stop();
    for (let i = 0; i < 300; i++) sim.tick();
    const linkRows = graph.links.map((l) => {
      const sNode = simNodes.find((n) => n.id === l.source);
      const tNode = simNodes.find((n) => n.id === l.target);
      return {
        sourceId: l.source,
        targetId: l.target,
        sx: sNode?.x ?? 0,
        sy: sNode?.y ?? 0,
        tx: tNode?.x ?? 0,
        ty: tNode?.y ?? 0,
        value: l.value,
      };
    });
    setLayout({
      nodes: simNodes.map((n) => ({
        id: n.id,
        x: n.x ?? 0,
        y: n.y ?? 0,
        kind: n.kind,
        label: n.label,
        value: n.value,
        healthy: n.healthy,
        hosts: n.hosts,
      })),
      links: linkRows,
    });
  // graph.nodes count changes on cluster add/remove; we only want to
  // re-tick when the membership changes, NOT when the values change
  // (avoids jiggling).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clusterSetKey]);

  // Click handler — emit a window event the parent EnvoyAdminSummaryCard
  // listens for (it owns the ClusterDetailDrawer). Lightweight global
  // bus rather than threading a callback through every parent.
  const handleClusterClick = (id: string) => {
    if (id === "__internet__" || id === "__gateway__") return;
    window.dispatchEvent(
      new CustomEvent("media-stack:cluster-drill", { detail: { cluster: id } }),
    );
  };

  if (q.isLoading) {
    return (
      <Card data-testid="topology-graph-card-loading">
        <CardHeader>
          <CardTitle>Service map</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-80 w-full rounded-md" />
        </CardContent>
      </Card>
    );
  }

  if (!graph || !layout) {
    return (
      <Card data-testid="topology-graph-card-empty">
        <CardHeader>
          <CardTitle>Service map</CardTitle>
          <CardDescription>
            No traffic recorded yet — the graph appears once a
            cluster has handled a request.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex h-32 w-full items-center justify-center rounded border border-dashed border-border/60 bg-bg-1/40 text-xs text-fg-muted">
            Waiting for traffic…
          </div>
        </CardContent>
      </Card>
    );
  }

  const maxLinkValue = Math.max(1, ...layout.links.map((l) => l.value));

  return (
    <Card data-testid="topology-graph-card">
      <CardHeader>
        <CardTitle>Service map</CardTitle>
        <CardDescription>
          Live topology: <em>Internet → Envoy → upstream clusters</em>.
          Edge thickness = request volume; animated dashes show active
          flow. Cluster colour = host health (green / amber / red).
          Click any cluster node to drill into its full history.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="w-full overflow-x-auto">
          <svg
            ref={svgRef}
            viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
            className="h-[420px] w-full"
            role="img"
            aria-label="Live service topology graph"
            data-testid="topology-graph-svg"
          >
            <defs>
              <marker
                id="topology-arrow"
                viewBox="0 0 10 10"
                refX="10"
                refY="5"
                markerWidth="6"
                markerHeight="6"
                orient="auto-start-reverse"
              >
                <path
                  d="M 0 0 L 10 5 L 0 10 z"
                  fill="var(--color-fg-faint)"
                />
              </marker>
            </defs>
            {/* Edges */}
            <g>
              {layout.links.map((l, i) => {
                const w = 1 + (l.value / maxLinkValue) * 6;
                const speed = 1 + Math.min(8, (l.value / maxLinkValue) * 8);
                return (
                  <g key={`${l.sourceId}-${l.targetId}-${i}`}>
                    <line
                      x1={l.sx}
                      y1={l.sy}
                      x2={l.tx}
                      y2={l.ty}
                      stroke="var(--color-border)"
                      strokeWidth={w}
                      strokeOpacity={0.5}
                      data-testid={`topology-edge-${l.targetId}`}
                    />
                    {/* Animated dash overlay = "live flow". CSS keyframe
                        in tailwind isn't directly accessible from inline
                        SVG; use SMIL's animate which all evergreen
                        browsers support. */}
                    <line
                      x1={l.sx}
                      y1={l.sy}
                      x2={l.tx}
                      y2={l.ty}
                      stroke="var(--color-info)"
                      strokeWidth={Math.max(1, w / 2)}
                      strokeDasharray="6 6"
                      strokeOpacity={0.7}
                      pointerEvents="none"
                    >
                      <animate
                        attributeName="stroke-dashoffset"
                        from="0"
                        to="-12"
                        dur={`${1.5 / speed}s`}
                        repeatCount="indefinite"
                      />
                    </line>
                  </g>
                );
              })}
            </g>
            {/* Nodes */}
            <g>
              {layout.nodes.map((n) => {
                const r = nodeRadius(n);
                const fill = nodeFill(n);
                return (
                  <g
                    key={n.id}
                    transform={`translate(${n.x}, ${n.y})`}
                    style={{ cursor: n.kind === "cluster" ? "pointer" : "default" }}
                    onClick={() => handleClusterClick(n.id)}
                    data-testid={`topology-node-${n.id}`}
                  >
                    <circle
                      r={r}
                      fill={fill}
                      stroke="var(--color-bg-1)"
                      strokeWidth={2}
                    />
                    <text
                      textAnchor="middle"
                      dy={r + 14}
                      fontSize={11}
                      className="fill-[var(--color-fg)] font-mono"
                    >
                      {n.label}
                    </text>
                    {n.kind === "cluster" && n.hosts !== undefined ? (
                      <text
                        textAnchor="middle"
                        dy={r + 26}
                        fontSize={9}
                        className="fill-[var(--color-fg-muted)]"
                      >
                        {n.healthy}/{n.hosts} healthy · {n.value} req
                      </text>
                    ) : null}
                    {n.kind === "gateway" ? (
                      <text
                        textAnchor="middle"
                        dy={r + 26}
                        fontSize={9}
                        className="fill-[var(--color-fg-muted)]"
                      >
                        {n.value.toLocaleString()} requests served
                      </text>
                    ) : null}
                  </g>
                );
              })}
            </g>
          </svg>
        </div>
        <p className="mt-2 text-[11px] text-fg-muted">
          Layout is computed once per cluster-set change and frozen —
          numbers update on the existing 30s admin-summary cadence
          without re-flowing the graph.
        </p>
      </CardContent>
    </Card>
  );
}

function nodeRadius(n: { kind: string; value: number }): number {
  if (n.kind === "internet") return 18;
  if (n.kind === "gateway") return 24;
  // Cluster sized by request volume, log-ish so a 100k req cluster
  // doesn't dwarf a 100 req one.
  const base = 8;
  const scaled = Math.min(20, Math.log10(Math.max(1, n.value)) * 5);
  return base + scaled;
}

function nodeFill(n: {
  kind: string;
  healthy?: number;
  hosts?: number;
}): string {
  if (n.kind === "internet") return "var(--color-fg-muted)";
  if (n.kind === "gateway") return "var(--color-accent)";
  // Cluster — colour by health.
  if (n.hosts === undefined || n.hosts === 0) return "var(--color-fg-faint)";
  if (n.healthy === n.hosts) return "var(--color-success)";
  if ((n.healthy ?? 0) === 0) return "var(--color-danger)";
  return "var(--color-warning)";
}

function prettyCluster(name: string): string {
  if (name.startsWith("service_")) return name.slice("service_".length);
  return name;
}
