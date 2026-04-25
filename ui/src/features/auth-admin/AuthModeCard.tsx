import { useState } from "react";
import { AlertTriangle, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api";
import { asArray } from "@/lib/coerce";
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
  DialogClose,
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
  useAuthConfig,
  useAuthModes,
  useUpdateAuthConfig,
  type AuthMode,
} from "./hooks";

function explain(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}

function modeVariant(
  mode?: AuthMode,
): "success" | "info" | "warning" | "default" {
  if (!mode) return "default";
  if (mode === "none") return "warning";
  if (mode === "basic") return "default";
  if (mode === "authelia" || mode === "authelia+oidc") return "success";
  return "info";
}

function modeLabel(mode?: string): string {
  if (!mode) return "unknown";
  return mode;
}

const FALLBACK_MODES: readonly string[] = [
  "authelia",
  "authelia+oidc",
  "authentik",
  "basic",
  "none",
];

/**
 * Top-level operator surface for the global auth strategy. Shows the
 * current mode as a badge and gates a `<ChangeModeDialog />` behind a
 * destructive confirmation — the controller invalidates every session
 * when the mode changes, so accidental clicks lock everyone out.
 */
export function AuthModeCard() {
  const config = useAuthConfig();
  const modes = useAuthModes();

  const current = config.data?.mode;
  // The controller's /api/auth/modes returns either a list of bare
  // string keys ("authelia", "basic", "none") OR a list of objects
  // {key, display_name, description, gateway_auth, controller_auth,
  // provider_service}. Earlier code assumed strings only and
  // crashed (React error #31) when the controller returned objects.
  // Normalise both shapes into {key, label, description}.
  interface ModeOption {
    key: string;
    label: string;
    description: string;
  }
  const rawOptions = asArray<unknown>(modes.data?.modes);
  const normalised: ModeOption[] = rawOptions.map((entry) => {
    if (typeof entry === "string") {
      return { key: entry, label: entry, description: "" };
    }
    if (entry && typeof entry === "object") {
      const o = entry as Record<string, unknown>;
      const key = typeof o.key === "string" ? o.key : "";
      return {
        key,
        label:
          typeof o.display_name === "string" && o.display_name
            ? o.display_name
            : key,
        description:
          typeof o.description === "string" ? o.description : "",
      };
    }
    return { key: String(entry), label: String(entry), description: "" };
  }).filter((o) => o.key);
  const options: ModeOption[] = normalised.length > 0
    ? normalised
    : FALLBACK_MODES.map((m) => ({ key: m, label: m, description: "" }));

  return (
    <Card data-testid="auth-mode-card">
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
        <div className="flex flex-col gap-1.5">
          <CardTitle className="flex items-center gap-2">
            <ShieldCheck aria-hidden className="size-4 text-fg-muted" />
            Auth mode
          </CardTitle>
          <CardDescription>
            Global authentication strategy. Mode changes log every active
            session out.
          </CardDescription>
        </div>
        <ChangeModeDialog
          current={current}
          options={options}
          disabled={config.isLoading}
        />
      </CardHeader>
      <CardContent>
        {config.isLoading ? (
          <Skeleton className="h-8 w-40" data-testid="auth-mode-loading" />
        ) : config.error ? (
          <p
            role="alert"
            className="text-sm text-danger"
            data-testid="auth-mode-error"
          >
            {config.error.message}
          </p>
        ) : (
          <div className="flex items-center gap-3">
            <Badge
              variant={modeVariant(current)}
              data-testid="auth-mode-current"
            >
              {modeLabel(current)}
            </Badge>
            {current === "none" ? (
              <span
                className="flex items-center gap-1 text-xs text-warning"
                data-testid="auth-mode-warning-none"
              >
                <AlertTriangle aria-hidden className="size-3" />
                No authentication enabled
              </span>
            ) : null}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

interface ChangeModeDialogProps {
  current?: AuthMode;
  options: readonly { key: string; label: string; description: string }[];
  disabled?: boolean;
}

function ChangeModeDialog({
  current,
  options,
  disabled,
}: ChangeModeDialogProps) {
  const [open, setOpen] = useState(false);
  const [next, setNext] = useState<string>(current ?? options[0]?.key ?? "basic");
  const update = useUpdateAuthConfig();

  // Re-seed the picker each time the dialog opens so a stale local
  // selection doesn't carry over after the operator cancels and the
  // controller has refreshed.
  const onOpenChange = (o: boolean) => {
    setOpen(o);
    if (o) setNext(current ?? options[0]?.key ?? "basic");
  };

  const handleSubmit = () => {
    if (!next || next === current) {
      setOpen(false);
      return;
    }
    update.mutate(
      { mode: next },
      {
        onSuccess: () => {
          toast.success(`Auth mode set to ${next}`);
          setOpen(false);
        },
        onError: (err) =>
          toast.error(`Mode change failed: ${explain(err, "request failed")}`),
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogTrigger asChild>
        <Button
          variant="secondary"
          size="sm"
          disabled={disabled}
          data-testid="auth-mode-change-trigger"
        >
          Change mode
        </Button>
      </DialogTrigger>
      <DialogContent data-testid="auth-mode-dialog">
        <DialogHeader>
          <DialogTitle>Change auth mode</DialogTitle>
          <DialogDescription>
            Switching modes signs every user out and resets per-service
            policy back to the new mode's defaults. Confirm only if every
            operator has been notified.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="auth-mode-select">Mode</Label>
            <Select value={next} onValueChange={setNext}>
              <SelectTrigger
                id="auth-mode-select"
                data-testid="auth-mode-select"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {options.map((m) => (
                  <SelectItem key={m.key} value={m.key}>
                    {m.label}
                    {m.description ? (
                      <span className="ml-2 text-xs text-fg-muted">
                        — {m.description}
                      </span>
                    ) : null}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div
            role="alert"
            className="flex items-start gap-2 rounded-md border border-[color-mix(in_oklab,var(--color-danger)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-danger)_10%,transparent)] p-3 text-xs text-danger"
            data-testid="auth-mode-confirm-warning"
          >
            <AlertTriangle aria-hidden className="mt-0.5 size-3.5 shrink-0" />
            <span>
              This is a destructive change — it logs every active session
              out, including yours.
            </span>
          </div>
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button type="button" variant="secondary">
              Cancel
            </Button>
          </DialogClose>
          <Button
            type="button"
            variant="danger"
            onClick={handleSubmit}
            loading={update.isPending}
            disabled={!next || next === current}
            data-testid="auth-mode-confirm"
          >
            Confirm change
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
