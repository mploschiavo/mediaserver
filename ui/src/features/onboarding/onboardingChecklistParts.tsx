import type { JSX } from "react";
import { Link } from "@tanstack/react-router";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  Circle,
  XCircle,
} from "lucide-react";
import { cn } from "@/lib/cn";
import {
  ONBOARDING_STEP_ROUTES,
  type OnboardingStep,
  type OnboardingStepStatus,
} from "./hooks";
import { PROGRESSBAR_ROLE } from "./setupStatusConstants";
import { TONE_TEXT } from "./toneClasses";

const STATUS_TONE: Record<
  OnboardingStepStatus,
  {
    iconText: string;
    rowBorder: string;
    rowBg: string;
    icon: typeof Check;
  }
> = {
  ok: {
    iconText: TONE_TEXT.success,
    rowBorder: "border-success/30",
    rowBg: "bg-success/5",
    icon: Check,
  },
  warn: {
    iconText: TONE_TEXT.warning,
    rowBorder: "border-warning/40",
    rowBg: "bg-warning/8",
    icon: AlertTriangle,
  },
  pending: {
    iconText: TONE_TEXT.info,
    rowBorder: "border-info/35",
    rowBg: "bg-info/6",
    icon: Circle,
  },
  error: {
    iconText: TONE_TEXT.danger,
    rowBorder: "border-danger/45",
    rowBg: "bg-danger/8",
    icon: XCircle,
  },
};

export function stepRoute(step: OnboardingStep): string | undefined {
  return ONBOARDING_STEP_ROUTES[step.id];
}

export function StepRow({
  step,
  prominent,
}: {
  step: OnboardingStep;
  prominent: boolean;
}): JSX.Element {
  const tone = STATUS_TONE[step.status];
  const Icon = tone.icon;
  const route = stepRoute(step);
  const body = (
    <>
      <Icon
        aria-hidden
        className={cn("mt-0.5 size-4 shrink-0", tone.iconText)}
      />
      <div className="min-w-0 flex-1">
        <div
          className={cn(
            "text-sm",
            prominent ? "font-medium text-fg" : "text-fg-muted",
          )}
        >
          {step.label}
        </div>
        {step.detail ? (
          <div
            className="text-xs text-fg-muted"
            data-testid={`step-detail-${step.id}`}
          >
            {step.detail}
          </div>
        ) : null}
      </div>
      {prominent && route ? (
        <ArrowRight
          aria-hidden
          className="mt-1 size-3.5 shrink-0 text-fg-faint transition-transform group-hover:translate-x-0.5"
        />
      ) : null}
    </>
  );
  if (prominent) {
    return (
      <li>
        {route ? (
          <Link
            to={route}
            className={cn(
              "group flex items-start gap-2 rounded-md border p-3 transition-colors hover:bg-bg-1",
              tone.rowBorder,
              tone.rowBg,
            )}
            data-testid={`onboarding-step-${step.id}`}
          >
            {body}
          </Link>
        ) : (
          <div
            className={cn(
              "flex items-start gap-2 rounded-md border p-3",
              tone.rowBorder,
              tone.rowBg,
            )}
            data-testid={`onboarding-step-${step.id}`}
          >
            {body}
          </div>
        )}
      </li>
    );
  }
  return (
    <li
      className="flex items-start gap-2 text-xs"
      data-testid={`onboarding-step-${step.id}`}
    >
      <Check aria-hidden className="mt-0.5 size-3.5 shrink-0 text-success" />
      <div className="min-w-0 flex-1">
        <span className="text-fg-muted">{step.label}</span>
        {step.detail ? (
          <span className="ml-1.5 text-fg-faint">· {step.detail}</span>
        ) : null}
      </div>
    </li>
  );
}

export function OnboardingProgressBar({
  pct,
  firstRun,
}: {
  pct: number;
  firstRun: boolean;
}): JSX.Element {
  return (
    <div
      className="relative mt-3 h-1.5 w-full overflow-hidden rounded-full bg-bg-1"
      role={PROGRESSBAR_ROLE}
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
      data-testid="onboarding-progress-bar"
    >
      <div
        className={cn(
          "h-full rounded-full bg-gradient-to-r from-accent via-accent to-accent/80 transition-[width] duration-500 ease-out",
          firstRun && pct < 100 ? "animate-pulse" : "",
        )}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}
