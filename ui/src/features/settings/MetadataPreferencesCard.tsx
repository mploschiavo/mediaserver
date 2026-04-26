import { useEffect, useState } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Globe2, Save } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiErrorTile } from "@/components/ApiErrorTile";
import { fetcher } from "@/api/client";
import { toast } from "sonner";

function explain(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}

interface MetadataPreset {
  language: string;
  country: string;
  label: string;
}

interface MetadataSettings {
  language: string;
  country: string;
  source: "profile" | "defaults";
  presets: MetadataPreset[];
}

/**
 * Operator-facing language + country preferences card. Surfaces the
 * `metadata.language` / `metadata.country` knobs that drive
 * Sonarr/Radarr quality-profile language picks, Jellyfin user
 * preferred-language, and TMDB metadata fetches.
 *
 * The endpoint already exists (``GET / POST /api/metadata-settings``);
 * this card was the missing UI surface — operators previously had
 * to hand-edit the bootstrap profile YAML to change language.
 *
 * Two affordances:
 *   * **Preset picker** — a dropdown of common (language, country)
 *     pairs. Picking one fills both fields atomically; the bundled
 *     presets cover the top 30 globally. Operators with niche pairs
 *     edit the inputs directly.
 *   * **Manual inputs** — language code (BCP 47 short, e.g. "fr")
 *     and country code (ISO 3166-1 alpha-2, e.g. "FR"). Inline
 *     validation: 2–5 chars for language, 2 chars for country.
 *
 * After save, a bootstrap-required note tells the operator the
 * change isn't fully live until bootstrap re-runs the per-arr-app
 * config writer (typically 30–60s).
 */
export function MetadataPreferencesCard() {
  const qc = useQueryClient();
  const q = useQuery<MetadataSettings>({
    queryKey: ["metadata-settings"],
    queryFn: () => fetcher<MetadataSettings>("api/metadata-settings"),
    staleTime: 30_000,
  });

  const [language, setLanguage] = useState("");
  const [country, setCountry] = useState("");
  const [presetIdx, setPresetIdx] = useState<string>("");

  useEffect(() => {
    if (q.data) {
      setLanguage(q.data.language);
      setCountry(q.data.country);
      const matchIdx = q.data.presets.findIndex(
        (p) => p.language === q.data!.language && p.country === q.data!.country,
      );
      setPresetIdx(matchIdx >= 0 ? String(matchIdx) : "");
    }
  }, [q.data]);

  const mut = useMutation({
    mutationFn: (body: { language: string; country: string }) =>
      fetcher("api/metadata-settings", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      toast.success(
        "Language preferences saved — bootstrap will propagate to arr-apps + Jellyfin within ~60s.",
      );
      qc.invalidateQueries({ queryKey: ["metadata-settings"] });
    },
    onError: (err) =>
      toast.error(`Save failed: ${explain(err, "request failed")}`),
  });

  const dirty =
    !!q.data &&
    (language !== q.data.language || country !== q.data.country);

  const handlePresetChange = (idx: string) => {
    setPresetIdx(idx);
    if (!q.data) return;
    const preset = q.data.presets[Number(idx)];
    if (!preset) return;
    setLanguage(preset.language);
    setCountry(preset.country);
  };

  const handleSave = () => {
    const lang = language.trim().toLowerCase();
    const cnt = country.trim().toUpperCase();
    if (lang.length < 2 || lang.length > 5) {
      toast.error("Language code must be 2–5 chars (e.g. 'fr', 'pt-BR').");
      return;
    }
    if (cnt.length !== 2) {
      toast.error("Country code must be 2 chars (e.g. 'US', 'BR').");
      return;
    }
    mut.mutate({ language: lang, country: cnt });
  };

  return (
    <Card data-testid="metadata-preferences-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Globe2 className="size-4" aria-hidden />
          Language &amp; region
        </CardTitle>
        <CardDescription>
          The language + country pair that drives Sonarr/Radarr
          quality-profile language picks, Jellyfin's preferred
          language, and TMDB metadata fetches. Stored in the bootstrap
          profile YAML; bootstrap propagates changes to each arr-app
          on the next reconcile.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {q.isLoading ? (
          <Skeleton className="h-20 w-full" />
        ) : q.error ? (
          <ApiErrorTile error={q.error} onRetry={() => void q.refetch()} />
        ) : !q.data ? null : (
          <>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <div className="flex flex-col gap-1">
                <label
                  htmlFor="metadata-preset"
                  className="text-xs font-medium uppercase tracking-wide text-fg-faint"
                >
                  Preset
                </label>
                <select
                  id="metadata-preset"
                  value={presetIdx}
                  onChange={(e) => handlePresetChange(e.target.value)}
                  className="rounded-md border border-border bg-bg-1 px-2 py-1 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-ring"
                  data-testid="metadata-preset-select"
                >
                  <option value="">— pick a preset —</option>
                  {q.data.presets.map((p, i) => (
                    <option key={`${p.language}-${p.country}-${i}`} value={String(i)}>
                      {p.label} ({p.language}/{p.country})
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex flex-col gap-1">
                <label
                  htmlFor="metadata-language"
                  className="text-xs font-medium uppercase tracking-wide text-fg-faint"
                >
                  Language
                </label>
                <input
                  id="metadata-language"
                  type="text"
                  value={language}
                  onChange={(e) => {
                    setLanguage(e.target.value);
                    setPresetIdx("");
                  }}
                  placeholder="en"
                  maxLength={5}
                  className="rounded-md border border-border bg-bg-1 px-2 py-1 font-mono text-sm text-fg focus:outline-none focus:ring-2 focus:ring-ring"
                  data-testid="metadata-language-input"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label
                  htmlFor="metadata-country"
                  className="text-xs font-medium uppercase tracking-wide text-fg-faint"
                >
                  Country
                </label>
                <input
                  id="metadata-country"
                  type="text"
                  value={country}
                  onChange={(e) => {
                    setCountry(e.target.value);
                    setPresetIdx("");
                  }}
                  placeholder="US"
                  maxLength={2}
                  className="rounded-md border border-border bg-bg-1 px-2 py-1 font-mono text-sm text-fg uppercase focus:outline-none focus:ring-2 focus:ring-ring"
                  data-testid="metadata-country-input"
                />
              </div>
            </div>
            <p className="text-xs text-fg-muted">
              Source:{" "}
              <span className="font-mono">
                {q.data.source === "profile"
                  ? "profile YAML"
                  : "defaults"}
              </span>
              . Subtitle preferences are configured in Bazarr (out of
              scope for this card; Bazarr proxy is a follow-up).
            </p>
            <div className="flex justify-end">
              <Button
                onClick={handleSave}
                disabled={!dirty || mut.isPending}
                data-testid="metadata-save"
              >
                <Save className="size-3.5" />
                {mut.isPending ? "Saving…" : "Save preferences"}
              </Button>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
