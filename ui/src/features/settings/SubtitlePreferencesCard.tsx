import { useEffect, useMemo, useState } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Languages, Save, ExternalLink } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiErrorTile } from "@/components/ApiErrorTile";
import { fetcher } from "@/api/client";
import { toast } from "sonner";

interface BazarrLanguage {
  code: string;
  name: string;
  enabled?: boolean;
}

interface BazarrProfile {
  id: number | string;
  name: string;
  items: { code: string; forced?: boolean; hi?: boolean }[];
}

interface BazarrSubtitleConfig {
  available_languages: BazarrLanguage[];
  profiles: BazarrProfile[];
  default_profile_id?: number | string | null;
  errors?: string[];
}

/**
 * Surfaces Bazarr's subtitle-language preferences without duplicating
 * Bazarr's full language-profile UI (hearing-impaired toggles, forced
 * flags, cutoff scoring). The 80% case is "what languages do I want
 * subtitles in?" — this card answers that with a checkbox grid +
 * one-shot save.
 *
 * Profile-aware: if Bazarr has multiple profiles, the operator picks
 * which one to edit. Default-profile-id is highlighted. For richer
 * editing, the "Open Bazarr ↗" deep-link goes to Bazarr's own
 * settings page.
 */
export function SubtitlePreferencesCard() {
  const qc = useQueryClient();
  const q = useQuery<BazarrSubtitleConfig>({
    queryKey: ["bazarr-subtitle-config"],
    queryFn: () => fetcher<BazarrSubtitleConfig>("api/bazarr/subtitle-config"),
    staleTime: 60_000,
  });

  const profiles = q.data?.profiles ?? [];
  const enabledLangs = useMemo(
    () => (q.data?.available_languages ?? []).filter((l) => l.enabled !== false),
    [q.data],
  );

  const [profileId, setProfileId] = useState<string>("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [seedKey, setSeedKey] = useState("");

  useEffect(() => {
    if (!q.data) return;
    // Pick default profile (or first available) on first load.
    const defaultId = q.data.default_profile_id ?? profiles[0]?.id;
    if (!profileId && defaultId !== undefined && defaultId !== null) {
      setProfileId(String(defaultId));
    }
  }, [q.data, profiles, profileId]);

  // Re-seed checkboxes whenever the chosen profile or upstream config
  // changes — but only when the *content* changes, not the reference.
  useEffect(() => {
    if (!profileId) return;
    const profile = profiles.find((p) => String(p.id) === profileId);
    const codes = (profile?.items ?? []).map((i) => i.code).filter(Boolean);
    const key = `${profileId}|${codes.sort().join(",")}`;
    if (key !== seedKey) {
      setSelected(new Set(codes));
      setSeedKey(key);
    }
  }, [profileId, profiles, seedKey]);

  const mut = useMutation({
    mutationFn: (body: {
      profile_id: string | number;
      language_codes: string[];
    }) =>
      fetcher("api/bazarr/subtitle-languages", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      toast.success(
        "Subtitle languages updated. Bazarr starts downloading new subs on the next scan.",
      );
      qc.invalidateQueries({ queryKey: ["bazarr-subtitle-config"] });
    },
    onError: (err: unknown) =>
      toast.error(
        err instanceof Error ? err.message : "Save failed",
      ),
  });

  const handleToggle = (code: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  };

  const handleSave = () => {
    if (!profileId) {
      toast.error("Pick a profile first.");
      return;
    }
    if (selected.size === 0) {
      toast.error(
        "At least one language is required. Bazarr can't have a profile with zero items.",
      );
      return;
    }
    mut.mutate({
      profile_id: profileId,
      language_codes: [...selected].sort(),
    });
  };

  const dirty = (() => {
    if (!profileId) return false;
    const profile = profiles.find((p) => String(p.id) === profileId);
    const baseline = new Set((profile?.items ?? []).map((i) => i.code));
    if (baseline.size !== selected.size) return true;
    for (const c of baseline) if (!selected.has(c)) return true;
    return false;
  })();

  return (
    <Card data-testid="subtitle-preferences-card">
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <CardTitle className="flex items-center gap-2">
            <Languages className="size-4" aria-hidden />
            Subtitle preferences
          </CardTitle>
          <CardDescription>
            Languages Bazarr downloads subtitles in. Tick a language to
            include it; untick to skip. Hearing-impaired flags + forced-
            only options live in Bazarr's own UI — open the deep-link
            below for advanced editing.
          </CardDescription>
        </div>
        <Button asChild variant="outline" size="sm">
          <a
            href="/app/bazarr/#/settings/languages"
            target="_blank"
            rel="noreferrer noopener"
            data-testid="subtitle-open-bazarr"
          >
            Open Bazarr <ExternalLink className="size-3.5" aria-hidden />
          </a>
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {q.isLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : q.error ? (
          <ApiErrorTile error={q.error} onRetry={() => void q.refetch()} />
        ) : !q.data || profiles.length === 0 ? (
          <div className="rounded-md border border-dashed border-border p-3 text-sm text-fg-muted">
            No Bazarr language profiles found. Bazarr may be unreachable, or
            no profile has been created — visit Bazarr → Settings →
            Languages to create one.
            {q.data?.errors && q.data.errors.length > 0 ? (
              <ul className="mt-2 list-disc pl-4 text-xs">
                {q.data.errors.map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : (
          <>
            <div className="flex flex-col gap-1">
              <label
                htmlFor="bazarr-profile"
                className="text-xs font-medium uppercase tracking-wide text-fg-faint"
              >
                Profile
              </label>
              <select
                id="bazarr-profile"
                value={profileId}
                onChange={(e) => setProfileId(e.target.value)}
                className="rounded-md border border-border bg-bg-1 px-2 py-1 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-ring"
                data-testid="subtitle-profile-select"
              >
                {profiles.map((p) => (
                  <option key={p.id} value={String(p.id)}>
                    {p.name}
                    {String(q.data?.default_profile_id ?? "") === String(p.id)
                      ? " (default)"
                      : ""}
                  </option>
                ))}
              </select>
            </div>

            <div className="flex flex-col gap-2">
              <span className="text-xs font-medium uppercase tracking-wide text-fg-faint">
                Languages ({selected.size} selected)
              </span>
              <div
                className="grid grid-cols-2 gap-1 sm:grid-cols-4"
                data-testid="subtitle-language-grid"
              >
                {enabledLangs.length === 0 ? (
                  <span className="col-span-full text-sm text-fg-muted">
                    No languages enabled in Bazarr. Enable at least one in
                    Bazarr → Settings → Languages first.
                  </span>
                ) : (
                  enabledLangs.map((l) => {
                    const checked = selected.has(l.code);
                    return (
                      <label
                        key={l.code}
                        className={
                          "flex cursor-pointer items-center gap-2 rounded-md border px-2 py-1 text-xs transition-colors " +
                          (checked
                            ? "border-info bg-info/10 text-fg"
                            : "border-border bg-bg-1 text-fg-muted hover:bg-bg-2")
                        }
                        data-testid={`subtitle-lang-${l.code}`}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => handleToggle(l.code)}
                          className="size-3"
                        />
                        <span className="font-mono uppercase">{l.code}</span>
                        <span className="truncate">{l.name}</span>
                      </label>
                    );
                  })
                )}
              </div>
            </div>

            {selected.size > 0 ? (
              <div
                className="flex flex-wrap items-center gap-1.5 text-xs"
                data-testid="subtitle-selected-chips"
              >
                <span className="text-fg-faint">Selected:</span>
                {[...selected].sort().map((c) => (
                  <Badge key={c} variant="outline">
                    {c.toUpperCase()}
                  </Badge>
                ))}
              </div>
            ) : null}

            <div className="flex justify-end">
              <Button
                onClick={handleSave}
                disabled={!dirty || mut.isPending}
                data-testid="subtitle-save"
              >
                <Save className="size-3.5" />
                {mut.isPending ? "Saving…" : "Save subtitle languages"}
              </Button>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
