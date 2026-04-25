import { CalendarRange, Lock } from "lucide-react";
import { asArray } from "@/lib/coerce";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/layout/EmptyState";
import {
  ResponsiveTable,
  type ResponsiveTableColumn,
} from "@/components/layout/ResponsiveTable";
import { useEpgProviders, type EpgProvider } from "./hooks";

function providerKey(p: EpgProvider): string {
  return p.id?.trim() || p.name;
}

/**
 * Browse-only catalog of EPG (electronic program guide) providers the
 * controller knows about. The OpenAPI contract is intentionally loose
 * (`additionalProperties: true`); we render the strict `name`,
 * `base_url`, `requires_auth` slice.
 */
export function EpgProvidersCard() {
  const providers = useEpgProviders();
  const list = asArray<EpgProvider>(providers.data?.providers);

  return (
    <Card data-testid="epg-providers-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <CalendarRange aria-hidden className="size-4 text-fg-muted" />
          EPG providers
        </CardTitle>
        <CardDescription>
          Supported guide providers. Wire one up via the live-TV source
          editor above.
        </CardDescription>
      </CardHeader>
      <CardContent className="p-0">
        {providers.isLoading ? (
          <div className="space-y-2 p-6" data-testid="epg-providers-loading">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : providers.error ? (
          <p
            role="alert"
            className="px-6 py-4 text-sm text-danger"
            data-testid="epg-providers-error"
          >
            {providers.error.message}
          </p>
        ) : list.length === 0 ? (
          <div className="p-6">
            <EmptyState
              icon={CalendarRange}
              title="No EPG providers"
              description="The controller hasn't published any guide providers yet."
            />
          </div>
        ) : (
          <ProvidersTable providers={list} />
        )}
      </CardContent>
    </Card>
  );
}

/**
 * Pick the most informative URL field. Live providers carry
 * ``url_template`` (e.g. ``"https://iptv-epg.org/files/epg-{code}.xml"``);
 * a few legacy entries also have ``base_url``. Showing the template
 * tells the operator which catalog this entry belongs to and the
 * countries it covers (the ``{code}`` placeholder is the per-country
 * fan-out). Falling back to ``base_url`` covers the rare provider
 * with no template, and "—" only fires when both are missing.
 */
function providerUrl(p: EpgProvider): string {
  return p.url_template || p.base_url || "";
}

function ProvidersTable({ providers }: { providers: readonly EpgProvider[] }) {
  const columns: ResponsiveTableColumn<EpgProvider>[] = [
    {
      id: "name",
      header: "Name",
      cell: (row) => <span className="font-medium text-fg">{row.name}</span>,
    },
    {
      id: "url-template",
      header: "URL pattern",
      cell: (row) => {
        const url = providerUrl(row);
        return (
          <span
            className="font-mono text-xs text-fg-muted"
            title={url || row.notes || ""}
          >
            {url || "—"}
          </span>
        );
      },
    },
    {
      id: "auth",
      header: "Status",
      cell: (row) => {
        if (row.requires_auth) {
          return (
            <Badge variant="warning" data-testid={`epg-auth-${providerKey(row)}`}>
              <Lock aria-hidden className="size-3" /> requires auth
            </Badge>
          );
        }
        // Newer providers expose `enabled: false` to mean "shipped but
        // disabled in this profile". Treat as a distinct state — the
        // earlier "open" / "requires auth" binary missed it.
        if (row.enabled === false) {
          return <Badge variant="default">disabled</Badge>;
        }
        return <Badge variant="default">open</Badge>;
      },
    },
  ];

  return (
    <ResponsiveTable
      rows={[...providers]}
      rowKey={providerKey}
      columns={columns}
      card={(row) => {
        const url = providerUrl(row);
        return (
          <div
            className="flex flex-col gap-1"
            data-testid={`epg-provider-card-${providerKey(row)}`}
          >
            <span className="font-medium text-fg">{row.name}</span>
            <span
              className="truncate font-mono text-xs text-fg-muted"
              title={url || row.notes || ""}
            >
              {url || "—"}
            </span>
            <div>
              {row.requires_auth ? (
                <Badge variant="warning">
                  <Lock aria-hidden className="size-3" /> requires auth
                </Badge>
              ) : row.enabled === false ? (
                <Badge variant="default">disabled</Badge>
              ) : (
                <Badge variant="default">open</Badge>
              )}
            </div>
            {row.notes ? (
              <span className="text-xs text-fg-muted">{row.notes}</span>
            ) : null}
          </div>
        );
      }}
    />
  );
}
