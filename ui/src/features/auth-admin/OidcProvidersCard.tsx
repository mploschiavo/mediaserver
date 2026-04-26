import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Globe, Pencil } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api";
import { ApiErrorTile } from "@/components/ApiErrorTile";
import { asObjectMap } from "@/lib/coerce";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/layout/EmptyState";
import {
  useAuthConfig,
  useParseOidc,
  useUpdateAuthConfig,
  type ParsedOidcConfig,
} from "./hooks";

function explain(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}

interface ProviderFormState {
  client_id: string;
  client_secret: string;
  discovery_url: string;
  issuer: string;
  auth_url: string;
  token_url: string;
  userinfo_url: string;
  jwks_url: string;
  scopes: string;
}

const EMPTY_FORM: ProviderFormState = {
  client_id: "",
  client_secret: "",
  discovery_url: "",
  issuer: "",
  auth_url: "",
  token_url: "",
  userinfo_url: "",
  jwks_url: "",
  scopes: "openid profile email",
};

function pickString(map: Record<string, unknown>, key: string): string {
  const v = map[key];
  return typeof v === "string" ? v : "";
}

function formFromConfig(
  oidcConfig: Record<string, unknown> | undefined,
): ProviderFormState {
  if (!oidcConfig) return EMPTY_FORM;
  const scopesRaw = oidcConfig.scopes;
  const scopes = Array.isArray(scopesRaw)
    ? (scopesRaw as unknown[])
        .filter((s): s is string => typeof s === "string")
        .join(" ")
    : typeof scopesRaw === "string"
      ? scopesRaw
      : EMPTY_FORM.scopes;
  return {
    client_id: pickString(oidcConfig, "client_id"),
    client_secret: pickString(oidcConfig, "client_secret"),
    discovery_url: pickString(oidcConfig, "discovery_url"),
    issuer: pickString(oidcConfig, "issuer"),
    auth_url: pickString(oidcConfig, "auth_url"),
    token_url: pickString(oidcConfig, "token_url"),
    userinfo_url: pickString(oidcConfig, "userinfo_url"),
    jwks_url: pickString(oidcConfig, "jwks_url"),
    scopes: scopes || EMPTY_FORM.scopes,
  };
}

function configFromForm(f: ProviderFormState): Record<string, unknown> {
  const scopes = f.scopes
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  return {
    client_id: f.client_id.trim(),
    client_secret: f.client_secret,
    discovery_url: f.discovery_url.trim(),
    issuer: f.issuer.trim(),
    auth_url: f.auth_url.trim(),
    token_url: f.token_url.trim(),
    userinfo_url: f.userinfo_url.trim(),
    jwks_url: f.jwks_url.trim(),
    scopes,
  };
}

/**
 * Single-provider OIDC card. The controller models exactly one
 * configured OIDC provider — `oidc_provider` is its key
 * ("local" / "google" / a custom id), `oidc_config` carries its
 * parameters. We surface them as a single read panel; the edit
 * dialog mutates `oidc_config` via `POST /api/auth/config`.
 *
 * The wave-4 multi-provider list went away with the controller's
 * single-provider model. If a future controller reintroduces
 * multi-provider it will land under a new endpoint and we'll add a
 * sibling card.
 */
export function OidcProvidersCard() {
  const config = useAuthConfig();
  const provider = config.data?.oidc_provider ?? "";
  const oidcConfig = asObjectMap(config.data?.oidc_config);
  const isConfigured = Boolean(provider);
  const issuer = pickString(oidcConfig, "issuer");
  const clientId = pickString(oidcConfig, "client_id");

  return (
    <Card data-testid="oidc-providers-card">
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
        <div className="flex flex-col gap-1.5">
          <CardTitle className="flex items-center gap-2">
            <Globe aria-hidden className="size-4 text-fg-muted" />
            OIDC provider
          </CardTitle>
          <CardDescription>
            The single federated identity provider wired into the
            controller. Edit the discovery URL and credentials below.
          </CardDescription>
        </div>
        <ProviderDialog
          current={oidcConfig}
          providerKey={provider}
          trigger={
            <Button
              variant={isConfigured ? "secondary" : "primary"}
              size="sm"
              data-testid={
                isConfigured ? "oidc-edit-trigger" : "oidc-add-trigger"
              }
            >
              <Pencil aria-hidden /> {isConfigured ? "Edit" : "Configure"}
            </Button>
          }
        />
      </CardHeader>
      <CardContent className="p-0">
        {config.isLoading ? (
          <div className="space-y-2 p-6" data-testid="oidc-loading">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : config.error ? (
          <div className="px-6 py-4" data-testid="oidc-error">
            <ApiErrorTile
              error={config.error}
              onRetry={() => void config.refetch()}
            />
          </div>
        ) : !isConfigured ? (
          <div className="p-6">
            <EmptyState
              icon={Globe}
              title="No OIDC provider"
              description="Federate identity by registering an OIDC client."
            />
          </div>
        ) : (
          <div
            className="grid gap-x-6 gap-y-3 p-6 sm:grid-cols-2"
            data-testid="oidc-current"
          >
            <Field
              label="Provider"
              value={provider || "—"}
              testid="oidc-provider-key"
            />
            <Field label="Client id" value={clientId || "—"} mono />
            <Field
              label="Issuer"
              value={issuer || "—"}
              mono
              testid="oidc-issuer"
            />
            <Field
              label="Discovery URL"
              value={pickString(oidcConfig, "discovery_url") || "—"}
              mono
            />
            <div className="sm:col-span-2 flex flex-wrap items-center gap-2">
              <span className="text-xs text-fg-muted">Scopes</span>
              {(Array.isArray(oidcConfig.scopes)
                ? (oidcConfig.scopes as unknown[]).filter(
                    (s): s is string => typeof s === "string",
                  )
                : []
              ).map((s) => (
                <Badge key={s} variant="outline">
                  {s}
                </Badge>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Field({
  label,
  value,
  mono,
  testid,
}: {
  label: string;
  value: string;
  mono?: boolean;
  testid?: string;
}) {
  return (
    <div className="flex flex-col">
      <span className="text-xs text-fg-muted">{label}</span>
      <span
        className={mono ? "truncate font-mono text-fg" : "text-fg"}
        data-testid={testid}
      >
        {value}
      </span>
    </div>
  );
}

interface ProviderDialogProps {
  current: Record<string, unknown>;
  providerKey: string;
  trigger: ReactNode;
}

function ProviderDialog({
  current,
  providerKey,
  trigger,
}: ProviderDialogProps) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<ProviderFormState>(
    formFromConfig(current),
  );
  const parse = useParseOidc();
  const update = useUpdateAuthConfig();

  // Re-seed when the dialog opens so an in-flight edit doesn't bleed
  // into the next open.
  useEffect(() => {
    if (open) setForm(formFromConfig(current));
  }, [open, current]);

  const update1 = (patch: Partial<ProviderFormState>) =>
    setForm((prev) => ({ ...prev, ...patch }));

  const handleParse = () => {
    if (!form.discovery_url.trim()) {
      toast.error("Discovery URL required to parse");
      return;
    }
    parse.mutate(
      { discovery_url: form.discovery_url.trim() },
      {
        onSuccess: (data: ParsedOidcConfig) => {
          update1({
            issuer: data.issuer ?? form.issuer,
            auth_url: data.auth_url ?? form.auth_url,
            token_url: data.token_url ?? form.token_url,
            userinfo_url: data.userinfo_url ?? form.userinfo_url,
            jwks_url: data.jwks_url ?? form.jwks_url,
            scopes:
              Array.isArray(data.scopes_supported) &&
              data.scopes_supported.length > 0
                ? data.scopes_supported.join(" ")
                : form.scopes,
          });
          toast.success("Discovery document parsed");
        },
        onError: (err) =>
          toast.error(`Parse failed: ${explain(err, "request failed")}`),
      },
    );
  };

  const handleSubmit = (ev: FormEvent) => {
    ev.preventDefault();
    if (!form.client_id.trim()) {
      toast.error("Client ID required");
      return;
    }
    const next = configFromForm(form);
    update.mutate(
      {
        oidc_provider: providerKey || "custom",
        oidc_config: next,
      },
      {
        onSuccess: () => {
          toast.success("OIDC provider saved");
          setOpen(false);
        },
        onError: (err) =>
          toast.error(`Save failed: ${explain(err, "request failed")}`),
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="max-w-2xl" data-testid="oidc-edit-dialog">
        <DialogHeader>
          <DialogTitle>Configure OIDC provider</DialogTitle>
          <DialogDescription>
            Paste the discovery URL and click Parse to auto-populate the
            endpoint fields, then review before saving.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={handleSubmit}
          aria-label="Configure OIDC provider"
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="oidc-discovery">Discovery URL</Label>
            <div className="flex items-center gap-2">
              <Input
                id="oidc-discovery"
                type="url"
                placeholder="https://issuer.example/.well-known/openid-configuration"
                value={form.discovery_url}
                onChange={(e) => update1({ discovery_url: e.target.value })}
                data-testid="oidc-discovery"
              />
              <Button
                type="button"
                variant="secondary"
                onClick={handleParse}
                loading={parse.isPending}
                data-testid="oidc-parse"
              >
                Parse
              </Button>
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <FormField
              id="oidc-client-id"
              label="Client ID"
              value={form.client_id}
              onChange={(v) => update1({ client_id: v })}
              testid="oidc-client-id"
              required
            />
            <FormField
              id="oidc-client-secret"
              label="Client secret"
              type="password"
              value={form.client_secret}
              onChange={(v) => update1({ client_secret: v })}
              testid="oidc-client-secret"
            />
            <FormField
              id="oidc-issuer"
              label="Issuer"
              value={form.issuer}
              onChange={(v) => update1({ issuer: v })}
              testid="oidc-issuer-input"
            />
            <FormField
              id="oidc-auth-url"
              label="Authorization URL"
              value={form.auth_url}
              onChange={(v) => update1({ auth_url: v })}
              testid="oidc-auth-url"
            />
            <FormField
              id="oidc-token-url"
              label="Token URL"
              value={form.token_url}
              onChange={(v) => update1({ token_url: v })}
              testid="oidc-token-url"
            />
            <FormField
              id="oidc-userinfo-url"
              label="Userinfo URL"
              value={form.userinfo_url}
              onChange={(v) => update1({ userinfo_url: v })}
              testid="oidc-userinfo-url"
            />
            <FormField
              id="oidc-jwks-url"
              label="JWKS URL"
              value={form.jwks_url}
              onChange={(v) => update1({ jwks_url: v })}
              testid="oidc-jwks-url"
            />
          </div>

          <FormField
            id="oidc-scopes"
            label="Scopes (space separated)"
            value={form.scopes}
            onChange={(v) => update1({ scopes: v })}
            testid="oidc-scopes"
          />

          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="secondary">
                Cancel
              </Button>
            </DialogClose>
            <Button
              type="submit"
              variant="primary"
              loading={update.isPending}
              data-testid="oidc-submit"
            >
              Save
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function FormField({
  id,
  label,
  value,
  onChange,
  testid,
  type,
  required,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
  testid: string;
  type?: string;
  required?: boolean;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type={type ?? "text"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        data-testid={testid}
        required={required}
      />
    </div>
  );
}
