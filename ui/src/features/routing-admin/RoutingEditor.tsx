import { useId, useMemo, useState } from "react";
import { toast } from "sonner";
import { ApiError, type RoutingStrategyShape } from "@/api";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useUpdateRouting,
  type RoutingConfigInput,
  type RoutingStrategyValue,
} from "./hooks";

interface RoutingEditorProps {
  /** Current strategy snapshot — used to seed the form. */
  initial?: RoutingStrategyShape;
  onCancel?: () => void;
  onSaved?: () => void;
}

interface FormState {
  strategy: RoutingStrategyValue;
  base_domain: string;
  external_hostname: string;
}

const STRATEGIES: readonly RoutingStrategyValue[] = [
  "subdomain",
  "path",
  "hybrid",
];

function isStrategyValue(v: string): v is RoutingStrategyValue {
  return STRATEGIES.includes(v as RoutingStrategyValue);
}

/** Map the `RoutingStrategyShape` (read shape) onto the form state. */
function seedForm(initial?: RoutingStrategyShape): FormState {
  const raw = initial?.strategy;
  let strategy: RoutingStrategyValue = "subdomain";
  // The controller's strategy enum is now `path | subdomain | hybrid`
  // (v1.3.2 OpenAPI tightening). Pre-1.3.2 callers may pass the
  // legacy `path-prefix` literal; handle that defensively via cast.
  if (raw === "path" || (raw as string) === "path-prefix") strategy = "path";
  else if (raw === "subdomain") strategy = "subdomain";
  else if (raw === "hybrid") strategy = "hybrid";
  return {
    strategy,
    base_domain: initial?.base_domain ?? "",
    external_hostname: initial?.external_hostname ?? "",
  };
}

/** Hostname / domain validator — permissive enough for `.local` / IPs. */
function looksLikeHost(s: string): boolean {
  if (!s) return false;
  // Reject spaces, schemes, paths.
  if (/[\s/]/.test(s)) return false;
  if (s.includes("://")) return false;
  // Must contain at least one alphanumeric.
  return /[a-z0-9]/i.test(s);
}

interface FormErrors {
  base_domain?: string;
  external_hostname?: string;
}

function validate(form: FormState): FormErrors {
  const errors: FormErrors = {};
  if (form.base_domain && !looksLikeHost(form.base_domain)) {
    errors.base_domain = "Looks invalid — no spaces, schemes, or paths.";
  }
  if (form.external_hostname && !looksLikeHost(form.external_hostname)) {
    errors.external_hostname =
      "Looks invalid — no spaces, schemes, or paths.";
  }
  return errors;
}

/** Build the diff payload — only include keys the operator changed. */
function buildPayload(
  form: FormState,
  initial: FormState,
): RoutingConfigInput {
  const payload: RoutingConfigInput = {};
  if (form.strategy !== initial.strategy) payload.strategy = form.strategy;
  if (form.base_domain !== initial.base_domain) {
    payload.base_domain = form.base_domain;
  }
  if (form.external_hostname !== initial.external_hostname) {
    payload.gateway_host = form.external_hostname;
  }
  return payload;
}

export function RoutingEditor({
  initial,
  onCancel,
  onSaved,
}: RoutingEditorProps) {
  const [form, setForm] = useState<FormState>(() => seedForm(initial));
  const [showPreview, setShowPreview] = useState(false);
  const update = useUpdateRouting();

  const baseId = useId();
  const initialState = useMemo(() => seedForm(initial), [initial]);
  const errors = useMemo(() => validate(form), [form]);
  const payload = useMemo(
    () => buildPayload(form, initialState),
    [form, initialState],
  );
  const hasErrors = Object.values(errors).some(Boolean);
  const hasChanges = Object.keys(payload).length > 0;

  const onSubmit = () => {
    if (hasErrors || !hasChanges) return;
    update.mutate(payload, {
      onSuccess: (result) => {
        const changed = result?.changed?.length ?? 0;
        toast.success(
          changed > 0
            ? `Routing updated — ${changed} key${changed === 1 ? "" : "s"} changed.`
            : "Routing updated.",
        );
        onSaved?.();
      },
      onError: (err) => {
        const msg =
          err instanceof ApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : "Routing update failed";
        toast.error(msg);
      },
    });
  };

  return (
    <Card data-testid="routing-editor">
      <CardHeader>
        <CardTitle>Edit routing</CardTitle>
        <CardDescription>
          Strategy + domain controls applied to the Envoy gateway. Changes
          persist to the routing-overrides file and trigger an envoy-config
          reload.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-col gap-2">
          <Label htmlFor={`${baseId}-strategy`}>Strategy</Label>
          <Select
            value={form.strategy}
            onValueChange={(v) => {
              if (isStrategyValue(v)) {
                setForm((f) => ({ ...f, strategy: v }));
              }
            }}
          >
            <SelectTrigger
              id={`${baseId}-strategy`}
              data-testid="routing-editor-strategy"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STRATEGIES.map((s) => (
                <SelectItem key={s} value={s}>
                  {s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex flex-col gap-2">
          <Label htmlFor={`${baseId}-base`}>Base domain</Label>
          <Input
            id={`${baseId}-base`}
            value={form.base_domain}
            onChange={(e) =>
              setForm((f) => ({ ...f, base_domain: e.target.value.trim() }))
            }
            placeholder="example.com"
            data-testid="routing-editor-base-domain"
          />
          {errors.base_domain ? (
            <p className="text-xs text-danger" role="alert">
              {errors.base_domain}
            </p>
          ) : null}
        </div>

        <div className="flex flex-col gap-2">
          <Label htmlFor={`${baseId}-external`}>External hostname</Label>
          <Input
            id={`${baseId}-external`}
            value={form.external_hostname}
            onChange={(e) =>
              setForm((f) => ({
                ...f,
                external_hostname: e.target.value.trim(),
              }))
            }
            placeholder="apps.media-stack.example.com"
            data-testid="routing-editor-external-hostname"
          />
          {errors.external_hostname ? (
            <p className="text-xs text-danger" role="alert">
              {errors.external_hostname}
            </p>
          ) : null}
        </div>

        {showPreview && hasChanges ? (
          <div
            className="rounded-md border border-border bg-bg-2 p-3 text-xs"
            data-testid="routing-editor-preview"
          >
            <div className="mb-1 font-medium text-fg">Diff preview</div>
            <pre className="overflow-x-auto whitespace-pre font-mono text-fg-muted">
              {JSON.stringify(payload, null, 2)}
            </pre>
          </div>
        ) : null}

        <div className="flex flex-wrap items-center justify-end gap-2 pt-2">
          <Button
            variant="secondary"
            size="sm"
            onClick={onCancel}
            data-testid="routing-editor-cancel"
            disabled={update.isPending}
          >
            Cancel
          </Button>
          <Button
            variant="default"
            size="sm"
            onClick={() => setShowPreview((v) => !v)}
            disabled={!hasChanges}
            data-testid="routing-editor-preview-toggle"
          >
            {showPreview ? "Hide preview" : "Preview"}
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={onSubmit}
            disabled={!hasChanges || hasErrors || update.isPending}
            loading={update.isPending}
            data-testid="routing-editor-submit"
          >
            Save changes
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
