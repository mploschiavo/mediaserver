import { useMemo, useState } from "react";
import { Globe2, Search } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api";
import { asArray } from "@/lib/coerce";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/layout/EmptyState";
import {
  useIptvCountries,
  useLivetvSources,
  useSaveLivetvSources,
  type IptvCountry,
  type LivetvSource,
} from "./hooks";

function explain(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}

function countryLabel(c: IptvCountry): string {
  return c.name?.trim() || c.code;
}

/**
 * Country picker for the bundled IPTV packs the controller curates.
 *
 * The controller doesn't yet expose a dedicated "apply pack" verb, so
 * "Apply default pack" is implemented by appending the chosen country's
 * `m3u_url` to the existing live-TV source list and re-posting via
 * `useSaveLivetvSources()`. When the chosen country has no `m3u_url`
 * the card stays in browse-only mode (the apply button is disabled).
 */
export function IptvCountriesCard() {
  const countries = useIptvCountries();
  const livetvSources = useLivetvSources();
  const save = useSaveLivetvSources();

  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string>("");

  const list = asArray<IptvCountry>(countries.data?.countries);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return list;
    return list.filter((c) => {
      const haystack = `${countryLabel(c)} ${c.code}`.toLowerCase();
      return haystack.includes(q);
    });
  }, [list, query]);

  const selectedCountry = useMemo(
    () => list.find((c) => c.code === selected),
    [list, selected],
  );

  const canApply =
    !!selectedCountry?.m3u_url ||
    !!selectedCountry?.tuner_url ||
    !!selectedCountry?.guide_url;

  const handleApply = () => {
    if (!selectedCountry) {
      toast.error("Pick a country first");
      return;
    }
    // Real /api/iptv-countries shape carries `tuner_url` (M3U) plus
    // an optional `guide_url` (XMLTV EPG). Either may be empty when
    // the upstream catalog doesn't supply one for that country.
    const tunerUrl =
      typeof selectedCountry.tuner_url === "string"
        ? selectedCountry.tuner_url
        : typeof selectedCountry.m3u_url === "string"
          ? selectedCountry.m3u_url
          : "";
    const guideUrl =
      typeof selectedCountry.guide_url === "string"
        ? selectedCountry.guide_url
        : "";
    if (!tunerUrl && !guideUrl) {
      toast.error("Country has no default URLs bundled");
      return;
    }
    const existingTuners = asArray<LivetvSource>(livetvSources.data?.tuners);
    const existingGuides = asArray<LivetvSource>(livetvSources.data?.guides);
    const name = `IPTV ${countryLabel(selectedCountry)}`;
    const body: {
      tuners?: readonly LivetvSource[];
      guides?: readonly LivetvSource[];
    } = {};
    if (tunerUrl && !existingTuners.some((t) => t.url === tunerUrl)) {
      body.tuners = [...existingTuners, { name, url: tunerUrl }];
    }
    if (guideUrl && !existingGuides.some((g) => g.url === guideUrl)) {
      body.guides = [...existingGuides, { name, url: guideUrl }];
    }
    if (Object.keys(body).length === 0) {
      toast.error(`${name} already added`);
      return;
    }
    save.mutate(body, {
      onSuccess: () => toast.success(`Applied ${name}`),
      onError: (err) =>
        toast.error(`Apply failed: ${explain(err, "request failed")}`),
    });
  };

  return (
    <Card data-testid="iptv-countries-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Globe2 aria-hidden className="size-4 text-fg-muted" />
          IPTV countries
        </CardTitle>
        <CardDescription>
          Browse curated country presets. Pick one to add its default M3U
          to your live-TV sources.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {countries.isLoading ? (
          <div className="space-y-2" data-testid="iptv-countries-loading">
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-32 w-full" />
          </div>
        ) : countries.error ? (
          <p
            role="alert"
            className="text-sm text-danger"
            data-testid="iptv-countries-error"
          >
            {countries.error.message}
          </p>
        ) : list.length === 0 ? (
          <EmptyState
            icon={Globe2}
            title="No countries available"
            description="The controller hasn't published any IPTV-country presets."
          />
        ) : (
          <>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="iptv-search">Search</Label>
              <div className="relative">
                <Search
                  aria-hidden
                  className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-fg-muted"
                />
                <Input
                  id="iptv-search"
                  className="pl-8"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search by name or code"
                  data-testid="iptv-search"
                />
              </div>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="iptv-country">Country</Label>
              <select
                id="iptv-country"
                value={selected}
                onChange={(e) => setSelected(e.target.value)}
                className="h-9 rounded-md border border-input bg-bg-1 px-3 text-sm text-fg shadow-sm focus-visible:border-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-bg"
                data-testid="iptv-country"
                size={Math.min(8, Math.max(4, filtered.length))}
              >
                <option value="" disabled>
                  Select a country…
                </option>
                {filtered.map((c) => {
                  // A country is APPLY-CAPABLE when ANY of its bundled
                  // URLs is present. The earlier check read only
                  // `m3u_url` (a legacy alias the live API never
                  // emits) so every country in the list rendered as
                  // "browse-only" — including the dozens that ship
                  // a real `tuner_url` and/or `guide_url`. Mirror the
                  // canApply predicate above so the dropdown label
                  // and the apply button stay in sync.
                  const hasAnyUrl = !!(c.tuner_url || c.guide_url || c.m3u_url);
                  return (
                    <option key={c.code} value={c.code}>
                      {countryLabel(c)} ({c.code})
                      {hasAnyUrl ? "" : " — browse-only"}
                    </option>
                  );
                })}
              </select>
            </div>
            <div className="flex items-center justify-end">
              <Button
                variant="primary"
                onClick={handleApply}
                disabled={!canApply}
                loading={save.isPending}
                data-testid="iptv-apply"
              >
                Apply default pack
              </Button>
            </div>
            {selectedCountry && !canApply ? (
              <p
                className="text-xs text-fg-muted"
                data-testid="iptv-readonly-note"
              >
                {countryLabel(selectedCountry)} has no default M3U — the
                pack is browse-only.
              </p>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}
