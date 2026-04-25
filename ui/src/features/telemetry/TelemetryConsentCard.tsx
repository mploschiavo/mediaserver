import { useEffect, useState } from "react";
import { Lock, Settings2, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

import { ApiError } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";

import {
  pickCategories,
  pickConsentLevel,
  useSaveTelemetry,
  useTelemetry,
  type TelemetryConsentLevel,
} from "./hooks";

const CONSENT_OPTIONS: ReadonlyArray<{
  value: TelemetryConsentLevel;
  label: string;
  description: string;
}> = [
  {
    value: "none",
    label: "None",
    description: "No telemetry. The controller never sends anything.",
  },
  {
    value: "minimal",
    label: "Minimal",
    description:
      "Crash counts and version info only. No service inventory, no metrics.",
  },
  {
    value: "standard",
    label: "Standard",
    description:
      "Aggregate health-probe outcomes and feature usage counts. No identifiers.",
  },
  {
    value: "full",
    label: "Full",
    description:
      "Everything in Standard plus anonymised feature flow traces.",
  },
];

const AVAILABLE_CATEGORIES: ReadonlyArray<{ value: string; label: string }> = [
  { value: "crash", label: "Crash counts" },
  { value: "version", label: "Version + uptime" },
  { value: "feature_usage", label: "Feature usage" },
  { value: "health_probes", label: "Health-probe outcomes" },
  { value: "performance", label: "Performance metrics" },
  { value: "flow_traces", label: "Anonymised flow traces" },
];

function consentBadgeVariant(
  level: TelemetryConsentLevel,
): "default" | "info" | "warning" | "success" {
  switch (level) {
    case "none":
      return "default";
    case "minimal":
      return "info";
    case "standard":
      return "warning";
    case "full":
      return "success";
  }
}

function errMsg(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}

interface UpdateDialogProps {
  initialConsent: TelemetryConsentLevel;
  initialCategories: readonly string[];
  onSubmit: (next: {
    consent: TelemetryConsentLevel;
    categories: readonly string[];
  }) => void;
  pending: boolean;
}

function UpdateDialog({
  initialConsent,
  initialCategories,
  onSubmit,
  pending,
}: UpdateDialogProps) {
  const [open, setOpen] = useState(false);
  const [consent, setConsent] =
    useState<TelemetryConsentLevel>(initialConsent);
  const [categories, setCategories] = useState<Set<string>>(
    new Set(initialCategories),
  );

  // Re-seed when the parent prefs change while the dialog is closed.
  useEffect(() => {
    if (!open) {
      setConsent(initialConsent);
      setCategories(new Set(initialCategories));
    }
  }, [initialConsent, initialCategories, open]);

  const toggleCategory = (cat: string) => {
    setCategories((prev) => {
      const next = new Set(prev);
      if (next.has(cat)) next.delete(cat);
      else next.add(cat);
      return next;
    });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) {
          setConsent(initialConsent);
          setCategories(new Set(initialCategories));
        }
      }}
    >
      <DialogTrigger asChild>
        <Button
          type="button"
          variant="primary"
          size="sm"
          data-testid="telemetry-update-trigger"
        >
          <Settings2 aria-hidden className="size-3.5" />
          Update preferences
        </Button>
      </DialogTrigger>
      <DialogContent
        data-testid="telemetry-update-dialog"
        className="max-w-xl"
      >
        <DialogHeader>
          <DialogTitle>Update telemetry preferences</DialogTitle>
          <DialogDescription>
            We never collect personal data, content names, or paths.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="telemetry-consent">Consent level</Label>
            <Select
              value={consent}
              onValueChange={(v) => setConsent(v as TelemetryConsentLevel)}
            >
              <SelectTrigger
                id="telemetry-consent"
                data-testid="telemetry-consent-select"
              >
                <SelectValue placeholder="Pick a level" />
              </SelectTrigger>
              <SelectContent>
                {CONSENT_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-fg-muted">
              {CONSENT_OPTIONS.find((o) => o.value === consent)?.description ??
                ""}
            </p>
          </div>
          <fieldset className="flex flex-col gap-2">
            <legend className="text-sm font-medium text-fg">
              Categories you opt into
            </legend>
            <p className="text-xs text-fg-muted">
              Untick anything you do not want shared. The list is
              advisory — the consent level above is the hard ceiling.
            </p>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {AVAILABLE_CATEGORIES.map((cat) => {
                const checked = categories.has(cat.value);
                const id = `telemetry-cat-${cat.value}`;
                return (
                  <label
                    key={cat.value}
                    htmlFor={id}
                    className="flex items-center gap-2 rounded-md border border-border bg-bg-1 p-2 text-sm"
                  >
                    <input
                      id={id}
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleCategory(cat.value)}
                      data-testid={`telemetry-cat-${cat.value}`}
                      className="size-4 accent-accent"
                    />
                    <span className="text-fg">{cat.label}</span>
                  </label>
                );
              })}
            </div>
          </fieldset>
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="secondary"
            onClick={() => setOpen(false)}
            data-testid="telemetry-cancel"
          >
            Cancel
          </Button>
          <Button
            type="button"
            variant="primary"
            disabled={pending}
            loading={pending}
            onClick={() => {
              onSubmit({
                consent,
                categories: [...categories],
              });
              setOpen(false);
            }}
            data-testid="telemetry-submit"
          >
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * TelemetryConsentCard — surfaces the current consent state and
 * the categories the operator opted into. The "Update preferences"
 * Dialog drives the POST. Privacy-friendly default copy keeps the
 * promise explicit: no personal data, no content names, no paths.
 */
export function TelemetryConsentCard() {
  const prefs = useTelemetry();
  const save = useSaveTelemetry();

  const consent = pickConsentLevel(prefs.data);
  const categories = pickCategories(prefs.data);

  const handleSubmit = (next: {
    consent: TelemetryConsentLevel;
    categories: readonly string[];
  }) => {
    save.mutate(next, {
      onSuccess: () => toast.success("Telemetry preferences saved"),
      onError: (err) => toast.error(errMsg(err, "Save failed")),
    });
  };

  return (
    <Card data-testid="telemetry-consent-card">
      <CardHeader className="flex-row items-start justify-between gap-3 sm:items-center">
        <div className="flex flex-col gap-1.5">
          <CardTitle>Telemetry consent</CardTitle>
          <CardDescription>
            We never collect personal data, content names, or paths.
          </CardDescription>
        </div>
        <UpdateDialog
          initialConsent={consent}
          initialCategories={categories}
          onSubmit={handleSubmit}
          pending={save.isPending}
        />
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div
          role="status"
          data-testid="telemetry-privacy-banner"
          className="flex items-start gap-2 rounded-md border border-border bg-bg-1 p-3 text-xs text-fg-muted"
        >
          <Lock aria-hidden className="mt-0.5 size-4 shrink-0 text-info" />
          <span>
            Your consent is stored on the controller and applies to every
            session. Changes take effect on the next probe cycle.
          </span>
        </div>
        {prefs.isLoading ? (
          <div className="space-y-2" data-testid="telemetry-loading">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : prefs.error ? (
          <div
            role="alert"
            data-testid="telemetry-error"
            className="text-sm text-danger"
          >
            {prefs.error.message}
          </div>
        ) : (
          <>
            <div className="flex items-center gap-2 text-sm">
              <ShieldCheck
                aria-hidden
                className="size-4 shrink-0 text-fg-muted"
              />
              <span className="text-fg-muted">Consent level</span>
              <Badge
                variant={consentBadgeVariant(consent)}
                data-testid="telemetry-consent-badge"
              >
                {consent}
              </Badge>
            </div>
            <div className="flex flex-col gap-1.5">
              <span className="text-sm text-fg-muted">
                Opted-in categories
              </span>
              {categories.length === 0 ? (
                <span
                  className="text-xs text-fg-faint"
                  data-testid="telemetry-categories-empty"
                >
                  No categories shared.
                </span>
              ) : (
                <div
                  className="flex flex-wrap gap-1.5"
                  data-testid="telemetry-categories-list"
                >
                  {categories.map((c) => (
                    <Badge key={c} variant="default">
                      {c}
                    </Badge>
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
