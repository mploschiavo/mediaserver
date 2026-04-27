import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ShieldCheck } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useMediaIntegrityStatus } from "@/api/hooks";

/**
 * Per-adapter coverage chart for the media-integrity surface. Bars
 * are configured Servarr adapters (radarr / sonarr / lidarr / readarr
 * / bazarr) and the Y axis indicates whether an API key is wired up
 * for that adapter — green = wired, red = key missing.
 *
 * The full reconciler-output history needs a server-side rolling
 * buffer that doesn't exist yet (the existing /api/media-integrity
 * payload is one-shot), so this chart is the no-server-work version
 * that ships today and unblocks the charts-coverage ratchet for the
 * /media-integrity route.
 */
export function IntegrityAdapterChart() {
  const query = useMediaIntegrityStatus();
  const data = useMemo(() => buildSeries(query.data), [query.data]);

  return (
    <Card data-testid="integrity-adapter-chart">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck aria-hidden className="size-4" />
          Adapter coverage
        </CardTitle>
        <CardDescription>
          Each Servarr / Bazarr adapter wired into the integrity
          reconciler. Red bars mean the controller doesn't have an
          API key yet — fix from the Service settings page.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {query.isLoading ? (
          <Skeleton className="h-44 w-full rounded-md" />
        ) : query.error ? (
          <p
            role="alert"
            className="text-sm text-danger"
            data-testid="integrity-adapter-chart-error"
          >
            Couldn't load integrity status:{" "}
            {(query.error as Error).message}
          </p>
        ) : data.length === 0 ? (
          <p
            className="text-sm text-fg-muted"
            data-testid="integrity-adapter-chart-empty"
          >
            No integrity adapters configured yet. Bootstrap will wire
            them in once the Servarr containers come online.
          </p>
        ) : (
          <div
            className="h-44 w-full"
            data-testid="integrity-adapter-chart-area"
          >
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data} layout="vertical">
                <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
                <XAxis
                  type="number"
                  domain={[0, 1]}
                  ticks={[0, 1]}
                  stroke="var(--fg-faint)"
                  tick={{ fontSize: 10 }}
                  tickFormatter={(v) => (v === 1 ? "wired" : "missing")}
                />
                <YAxis
                  type="category"
                  dataKey="adapter"
                  stroke="var(--fg-faint)"
                  tick={{ fontSize: 11 }}
                  width={80}
                />
                <Tooltip
                  contentStyle={{
                    background: "var(--bg-2)",
                    border: "1px solid var(--border)",
                    fontSize: 12,
                  }}
                  formatter={(value) => {
                    const v = typeof value === "number" ? value : Number(value ?? 0);
                    return [v === 1 ? "wired" : "missing", "status"];
                  }}
                />
                <Bar dataKey="value" radius={[0, 2, 2, 0]}>
                  {data.map((row, i) => (
                    <Cell
                      key={i}
                      fill={row.value === 1 ? "#4ade80" : "#f87171"}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

interface ChartRow {
  adapter: string;
  value: 0 | 1;
}

interface StatusShape {
  servarr_adapters?: readonly string[];
  bazarr_present?: boolean;
  missing_api_keys?: readonly string[];
}

function buildSeries(raw: unknown): ChartRow[] {
  if (!raw || typeof raw !== "object") return [];
  const status = raw as StatusShape;
  const servarr = Array.isArray(status.servarr_adapters)
    ? status.servarr_adapters
    : [];
  const missing = new Set<string>(
    Array.isArray(status.missing_api_keys) ? status.missing_api_keys : [],
  );
  const rows: ChartRow[] = servarr.map((adapter) => ({
    adapter,
    value: missing.has(adapter) ? 0 : 1,
  }));
  if (status.bazarr_present === true) {
    rows.push({ adapter: "bazarr", value: missing.has("bazarr") ? 0 : 1 });
  }
  return rows;
}
