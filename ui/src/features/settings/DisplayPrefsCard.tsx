import { Save } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api";
import { asArray, asObjectMap } from "@/lib/coerce";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  useDisplayPreferences,
  useSaveDisplayPreferences,
  type DisplayPreferences,
} from "./hooks";
import { useEffect, useState } from "react";

function errMsg(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}

/**
 * Jellyfin display-preferences card (server-side). The endpoint is
 * `/api/display-preferences` — these are knobs the controller pushes
 * to Jellyfin web/emby clients, NOT the dashboard's own theme. The
 * wave-4 settings agent mistakenly framed this as browser-UI prefs;
 * the card now displays the real Jellyfin shape (enabled / backdrops
 * / custom_prefs / per_library_prefs / clients).
 *
 * The dashboard's own theme + density live in `next-themes` and
 * `localStorage`, NOT here. There's no controller endpoint for them
 * because they're per-browser state, not per-stack configuration.
 */
export function DisplayPrefsCard() {
  const prefs = useDisplayPreferences();
  const save = useSaveDisplayPreferences();

  const [enabled, setEnabled] = useState<boolean>(true);
  const [showBackdrop, setShowBackdrop] = useState<boolean>(true);

  useEffect(() => {
    if (!prefs.data) return;
    setEnabled(prefs.data.enabled !== false);
    setShowBackdrop(prefs.data.show_backdrop !== false);
  }, [prefs.data]);

  const handleSave = () => {
    if (save.isPending) return;
    const body: DisplayPreferences = {
      ...(prefs.data ?? {}),
      enabled,
      show_backdrop: showBackdrop,
    };
    save.mutate(body, {
      onSuccess: () => toast.success("Jellyfin display preferences saved"),
      onError: (err) => toast.error(errMsg(err, "Save failed")),
    });
  };

  const customPrefs = asObjectMap(prefs.data?.custom_prefs);
  const perLibrary = asObjectMap(prefs.data?.per_library_prefs);
  const clients = asArray<string>(prefs.data?.clients);
  const customPrefKeys = Object.keys(customPrefs).slice(0, 6);
  const libraryKeys = Object.keys(perLibrary);

  return (
    <Card data-testid="display-prefs-card">
      <CardHeader>
        <CardTitle>Jellyfin display preferences</CardTitle>
        <CardDescription>
          Server-side knobs the controller pushes to Jellyfin clients
          (web / emby). The dashboard&apos;s own theme lives in your
          browser, not here.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {prefs.isLoading ? (
          <div className="space-y-2" data-testid="display-prefs-loading">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : prefs.error ? (
          <div
            role="alert"
            data-testid="display-prefs-error"
            className="text-sm text-danger"
          >
            {prefs.error.message}
          </div>
        ) : (
          <>
            <ToggleRow
              id="display-prefs-enabled"
              label="Push display preferences to Jellyfin"
              description="When off, Jellyfin keeps whatever clients last set locally."
              checked={enabled}
              onChange={setEnabled}
              testid="display-prefs-enabled"
            />
            <ToggleRow
              id="display-prefs-backdrop"
              label="Show cinema backdrops"
              description="Toggles `enableBackdrops` for all client surfaces."
              checked={showBackdrop}
              onChange={setShowBackdrop}
              testid="display-prefs-backdrop"
            />

            <div
              className="flex flex-col gap-2"
              data-testid="display-prefs-clients"
            >
              <Label className="text-xs text-fg-muted">
                Clients receiving these prefs
              </Label>
              <div className="flex flex-wrap gap-1">
                {clients.length === 0 ? (
                  <span className="text-fg-muted">—</span>
                ) : (
                  clients.map((c) => (
                    <Badge key={c} variant="outline">
                      {c}
                    </Badge>
                  ))
                )}
              </div>
            </div>

            <div
              className="flex flex-col gap-2"
              data-testid="display-prefs-libraries"
            >
              <Label className="text-xs text-fg-muted">
                Per-library overrides
              </Label>
              <div className="flex flex-wrap gap-1">
                {libraryKeys.length === 0 ? (
                  <span className="text-fg-muted">—</span>
                ) : (
                  libraryKeys.map((k) => (
                    <Badge key={k} variant="info">
                      {k}
                    </Badge>
                  ))
                )}
              </div>
            </div>

            {customPrefKeys.length > 0 ? (
              <div
                className="flex flex-col gap-2"
                data-testid="display-prefs-custom"
              >
                <Label className="text-xs text-fg-muted">
                  Custom prefs (showing {customPrefKeys.length} of{" "}
                  {Object.keys(customPrefs).length})
                </Label>
                <ul className="flex flex-col gap-1 font-mono text-xs text-fg-muted">
                  {customPrefKeys.map((k) => (
                    <li key={k} className="flex gap-2">
                      <span className="min-w-40 truncate text-fg">{k}</span>
                      <span className="truncate">
                        {String(customPrefs[k])}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </>
        )}
        <div className="flex items-center justify-end">
          <Button
            variant="primary"
            onClick={handleSave}
            disabled={prefs.isLoading || save.isPending}
            loading={save.isPending}
            data-testid="display-prefs-save"
          >
            <Save aria-hidden /> Save
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function ToggleRow({
  id,
  label,
  description,
  checked,
  onChange,
  testid,
}: {
  id: string;
  label: string;
  description?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  testid: string;
}) {
  return (
    <label
      htmlFor={id}
      className="flex items-center justify-between gap-3 rounded-md border border-border bg-bg-1 px-3 py-2"
    >
      <div className="flex flex-col">
        <span className="text-sm font-medium">{label}</span>
        {description ? (
          <span className="text-xs text-fg-muted">{description}</span>
        ) : null}
      </div>
      <Switch
        id={id}
        checked={checked}
        onCheckedChange={onChange}
        data-testid={testid}
      />
    </label>
  );
}
