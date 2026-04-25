import {
  useEffect,
  useMemo,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { motion } from "framer-motion";
import { Lock, Save, ShieldCheck } from "lucide-react";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/cn";
import {
  usePasswordPolicy,
  useUpdatePasswordPolicy,
  type PasswordPolicyResponse,
  type PasswordPolicyValues,
  type PolicyBound,
} from "./hooks";

function explain(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}

/**
 * Form state mirrors the wire shape one-for-one so the save dispatch
 * is a memberwise copy. Every field is non-optional in the form (the
 * defensive seeding from `fromResponse` guarantees it) so the
 * controlled inputs never go `undefined`.
 */
interface FormState {
  min_length: number;
  require_uppercase: boolean;
  require_lowercase: boolean;
  require_digit: boolean;
  require_special: boolean;
  history_len: number;
  max_age_days: number;
  lockout_threshold: number;
  lockout_window_minutes: number;
}

/** Hydrate the form from the controller payload, falling back to
 * `bounds.*.default` when a field isn't on the policy yet. The legacy
 * `require_classes` is mapped to the boolean toggles when explicit
 * booleans aren't on the wire — this keeps the UI behaving correctly
 * against older controllers.
 */
function fromResponse(r: PasswordPolicyResponse | undefined): FormState {
  const policy = r?.policy ?? {};
  const bounds = r?.bounds ?? {};
  const num = (key: keyof FormState, fallback: number): number => {
    const raw = policy[key as string];
    if (typeof raw === "number" && Number.isFinite(raw)) return raw;
    const bnd = bounds[key as string];
    return typeof bnd?.default === "number" ? bnd.default : fallback;
  };
  // Boolean field with fall-through to the legacy require_classes int.
  const bool = (key: keyof FormState, legacyTrue: boolean): boolean => {
    const raw = policy[key as string];
    if (typeof raw === "boolean") return raw;
    return legacyTrue;
  };
  const legacyClasses =
    typeof policy.require_classes === "number" ? policy.require_classes : 3;
  // Historical "1 of 3 classes" interpretation: <4 classes ⇒ upper +
  // lower + digit; ==4 ⇒ also special. This is what the migration on
  // the backend persists, and we mirror it here so a controller that
  // hasn't migrated yet still hydrates the toggles sensibly.
  const legacyUpper = legacyClasses >= 1;
  const legacyLower = legacyClasses >= 1;
  const legacyDigit = legacyClasses >= 1;
  const legacySpecial = legacyClasses >= 4;
  return {
    min_length: num("min_length", 12),
    require_uppercase: bool("require_uppercase", legacyUpper),
    require_lowercase: bool("require_lowercase", legacyLower),
    require_digit: bool("require_digit", legacyDigit),
    require_special: bool("require_special", legacySpecial),
    history_len: num("history_len", 5),
    max_age_days: num("max_age_days", 0),
    lockout_threshold: num("lockout_threshold", 5),
    lockout_window_minutes: num("lockout_window_minutes", 15),
  };
}

export function PasswordPolicyCard() {
  const policy = usePasswordPolicy();
  const update = useUpdatePasswordPolicy();
  const [form, setForm] = useState<FormState>(() => fromResponse(undefined));

  useEffect(() => {
    if (policy.data) setForm(fromResponse(policy.data));
  }, [policy.data]);

  const bounds = policy.data?.bounds ?? {};

  const classCount = useMemo(
    () =>
      Number(form.require_uppercase) +
      Number(form.require_lowercase) +
      Number(form.require_digit) +
      Number(form.require_special),
    [
      form.require_uppercase,
      form.require_lowercase,
      form.require_digit,
      form.require_special,
    ],
  );

  const handleSubmit = (ev: FormEvent) => {
    ev.preventDefault();
    const body: PasswordPolicyValues = {
      min_length: form.min_length,
      require_uppercase: form.require_uppercase,
      require_lowercase: form.require_lowercase,
      require_digit: form.require_digit,
      require_special: form.require_special,
      history_len: form.history_len,
      max_age_days: form.max_age_days,
      lockout_threshold: form.lockout_threshold,
      lockout_window_minutes: form.lockout_window_minutes,
    };
    update.mutate(body, {
      onSuccess: () => toast.success("Password policy saved"),
      onError: (err) =>
        toast.error(`Save failed: ${explain(err, "request failed")}`),
    });
  };

  return (
    <Card data-testid="password-policy-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Lock aria-hidden className="size-4 text-fg-muted" />
          Password policy
        </CardTitle>
        <CardDescription>
          Controls every controller-issued password. Provider-managed
          identities (Authelia, Jellyfin) keep their own rules.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {policy.isLoading ? (
          <div className="space-y-3" data-testid="password-policy-loading">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : (
          <motion.form
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            className="flex flex-col gap-7"
            onSubmit={handleSubmit}
            data-testid="password-policy-form"
          >
            <Section title="Strength">
              <SliderField
                id="pwp-min"
                label="Minimum length"
                helper="Recommended: 14 characters (NIST 800-63B)"
                bound={bounds.min_length ?? { floor: 4, ceiling: 128, default: 12 }}
                value={form.min_length}
                onChange={(v) => setForm({ ...form, min_length: v })}
                inputTestid="pwp-min-length"
                sliderTestid="pwp-min-length-slider"
              />
            </Section>

            <Section
              title="Character classes"
              description="At least one of each enabled class must appear."
            >
              <div className="flex flex-col gap-2">
                <ToggleRow
                  id="pwp-upper"
                  label="Require uppercase letter"
                  hint="A–Z"
                  checked={form.require_uppercase}
                  onChange={(v) => setForm({ ...form, require_uppercase: v })}
                  testid="pwp-require-uppercase"
                />
                <ToggleRow
                  id="pwp-lower"
                  label="Require lowercase letter"
                  hint="a–z"
                  checked={form.require_lowercase}
                  onChange={(v) => setForm({ ...form, require_lowercase: v })}
                  testid="pwp-require-lowercase"
                />
                <ToggleRow
                  id="pwp-digit"
                  label="Require digit"
                  hint="0–9"
                  checked={form.require_digit}
                  onChange={(v) => setForm({ ...form, require_digit: v })}
                  testid="pwp-require-digit"
                />
                <ToggleRow
                  id="pwp-special"
                  label="Require special character"
                  hint="!@#$ …"
                  checked={form.require_special}
                  onChange={(v) => setForm({ ...form, require_special: v })}
                  testid="pwp-require-special"
                />
              </div>
              <div
                className="mt-3 flex items-center gap-2 text-xs text-fg-muted"
                data-testid="pwp-class-count"
              >
                <ShieldCheck aria-hidden className="size-3.5" />
                <span>
                  {classCount} of 4 classes required
                </span>
              </div>
            </Section>

            <Section title="History & rotation">
              <div className="grid gap-5 md:grid-cols-2">
                <SliderField
                  id="pwp-history"
                  label="Forbid re-use of last N passwords"
                  bound={
                    bounds.history_len ?? { floor: 0, ceiling: 20, default: 5 }
                  }
                  value={form.history_len}
                  onChange={(v) => setForm({ ...form, history_len: v })}
                  inputTestid="pwp-history-len"
                  sliderTestid="pwp-history-len-slider"
                />
                <SliderField
                  id="pwp-max-age"
                  label="Force rotation after N days"
                  helper="0 = never expire"
                  bound={
                    bounds.max_age_days ?? {
                      floor: 0,
                      ceiling: 365,
                      default: 0,
                    }
                  }
                  value={form.max_age_days}
                  onChange={(v) => setForm({ ...form, max_age_days: v })}
                  inputTestid="pwp-max-age-days"
                  sliderTestid="pwp-max-age-days-slider"
                />
              </div>
            </Section>

            <Section title="Lockout">
              <div className="grid gap-5 md:grid-cols-2">
                <SliderField
                  id="pwp-lockout-threshold"
                  label="Failed attempts before lockout"
                  bound={
                    bounds.lockout_threshold ?? {
                      floor: 0,
                      ceiling: 50,
                      default: 5,
                    }
                  }
                  value={form.lockout_threshold}
                  onChange={(v) =>
                    setForm({ ...form, lockout_threshold: v })
                  }
                  inputTestid="pwp-lockout-threshold"
                  sliderTestid="pwp-lockout-threshold-slider"
                />
                <SliderField
                  id="pwp-lockout-window"
                  label="Lockout duration (minutes)"
                  bound={
                    bounds.lockout_window_minutes ?? {
                      floor: 0,
                      ceiling: 1440,
                      default: 15,
                    }
                  }
                  value={form.lockout_window_minutes}
                  onChange={(v) =>
                    setForm({ ...form, lockout_window_minutes: v })
                  }
                  inputTestid="pwp-lockout-window"
                  sliderTestid="pwp-lockout-window-slider"
                />
              </div>
            </Section>

            <div className="flex justify-end">
              <Button
                type="submit"
                variant="primary"
                loading={update.isPending}
                data-testid="pwp-save"
              >
                <Save aria-hidden /> Save
              </Button>
            </div>
          </motion.form>
        )}
      </CardContent>
    </Card>
  );
}

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3">
      <header className="flex flex-col gap-0.5">
        <h3 className="text-sm font-semibold text-fg">{title}</h3>
        {description ? (
          <p className="text-xs text-fg-muted">{description}</p>
        ) : null}
      </header>
      {children}
    </section>
  );
}

function SliderField({
  id,
  label,
  helper,
  bound,
  value,
  onChange,
  inputTestid,
  sliderTestid,
}: {
  id: string;
  label: string;
  helper?: string;
  bound: PolicyBound;
  value: number;
  onChange: (v: number) => void;
  inputTestid: string;
  sliderTestid: string;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-3">
        <Label htmlFor={id} className="text-sm font-medium">
          {label}
        </Label>
        <Badge variant="outline" className="font-mono tabular-nums">
          {value}
        </Badge>
      </div>
      <div className="flex items-center gap-3">
        <Input
          id={id}
          type="number"
          value={value}
          min={bound.floor}
          max={bound.ceiling}
          placeholder={String(bound.default)}
          onChange={(e) => {
            const n = Number(e.target.value);
            if (Number.isFinite(n)) onChange(n);
          }}
          data-testid={inputTestid}
          className="w-24 tabular-nums"
        />
        <Slider
          aria-label={label}
          min={bound.floor}
          max={bound.ceiling}
          value={value}
          step={1}
          onValueChange={onChange}
          data-testid={sliderTestid}
          className="min-h-[44px] py-5"
        />
      </div>
      {helper ? (
        <span className="text-xs text-fg-muted">{helper}</span>
      ) : null}
    </div>
  );
}

function ToggleRow({
  id,
  label,
  hint,
  checked,
  onChange,
  testid,
}: {
  id: string;
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  testid: string;
}) {
  return (
    <label
      htmlFor={id}
      className={cn(
        "flex min-h-[44px] items-center justify-between gap-3 rounded-md border border-border bg-bg-1 px-3 py-2",
        "transition-colors duration-[var(--duration-base)] ease-[var(--ease-out)]",
        "hover:border-[color-mix(in_oklab,var(--color-accent)_35%,var(--color-border))]",
      )}
    >
      <div className="flex flex-col">
        <span className="text-sm font-medium">{label}</span>
        {hint ? (
          <span className="font-mono text-xs text-fg-muted">{hint}</span>
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
