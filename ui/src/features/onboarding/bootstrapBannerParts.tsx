import type { JSX } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  XCircle,
} from "lucide-react";
import { cn } from "@/lib/cn";
import type {
  SetupExperienceState,
  TimelineStep,
} from "./setupState";
import { PROGRESSBAR_ROLE, SetupStatus } from "./setupStatusConstants";
import { TONE_TEXT } from "./toneClasses";

const TONE_ICON: Record<SetupExperienceState["statusTone"], string> = TONE_TEXT;

const PROGRESS_FILL: Record<SetupExperienceState["statusTone"], string> = {
  info: "from-info via-accent to-info/80",
  success: "from-success via-success to-success/70",
  warning: "from-warning via-warning to-warning/70",
  danger: "from-danger via-danger to-danger/70",
};

export function HeroIcon({
  phase,
  tone,
}: {
  phase: SetupExperienceState["phase"];
  tone: SetupExperienceState["statusTone"];
}): JSX.Element {
  const iconClass = cn("size-9 sm:size-10", TONE_ICON[tone]);
  if (phase === SetupStatus.Complete) {
    return (
      <div
        className="rounded-full bg-success/10 p-3 motion-safe:animate-[pulse_2.4s_ease-in-out_infinite]"
        aria-hidden
      >
        <CheckCircle2 className={iconClass} />
      </div>
    );
  }
  if (
    phase === SetupStatus.Failed ||
    phase === SetupStatus.Cancelled ||
    phase === SetupStatus.TimedOut
  ) {
    return (
      <div className="rounded-full bg-danger/10 p-3" aria-hidden>
        <XCircle className={iconClass} />
      </div>
    );
  }
  if (phase === SetupStatus.CompleteWithWarnings) {
    return (
      <div className="rounded-full bg-warning/10 p-3" aria-hidden>
        <AlertTriangle className={iconClass} />
      </div>
    );
  }
  return (
    <div
      className="rounded-full bg-info/10 p-3 motion-safe:animate-[pulse_3s_ease-in-out_infinite]"
      aria-hidden
    >
      <Loader2 className={cn(iconClass, "motion-safe:animate-spin")} />
    </div>
  );
}

export function ProgressBar({
  pct,
  tone,
}: {
  pct: number;
  tone: SetupExperienceState["statusTone"];
}): JSX.Element {
  return (
    <div
      className="relative h-1.5 w-full overflow-hidden rounded-full bg-bg-3"
      role={PROGRESSBAR_ROLE}
      aria-valuenow={Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
      data-testid="bootstrap-progress-banner-bar"
    >
      <div
        className={cn(
          "h-full rounded-full bg-gradient-to-r transition-[width] duration-700 ease-out motion-safe:animate-pulse",
          PROGRESS_FILL[tone],
        )}
        style={{ width: `${Math.max(2, Math.min(100, pct))}%` }}
      />
    </div>
  );
}

export function TimelineRow({ step }: { step: TimelineStep }): JSX.Element {
  const isRunning = step.status === SetupStatus.Running;
  const isOk = step.status === SetupStatus.Ok;
  const isError = step.status === SetupStatus.Error;
  const isSkipped = step.status === SetupStatus.Skipped;
  const Icon = isOk
    ? CheckCircle2
    : isError
      ? XCircle
      : isRunning
        ? Loader2
        : null;
  const iconColor = isOk
    ? TONE_TEXT.success
    : isError
      ? TONE_TEXT.danger
      : isRunning
        ? TONE_TEXT.info
        : isSkipped
          ? "text-fg-faint"
          : "text-fg-muted";
  return (
    <li
      className="flex items-center gap-3 px-3 py-2 text-xs"
      data-testid={`bootstrap-progress-step-${step.id}`}
      data-status={step.status}
    >
      {Icon ? (
        <Icon
          aria-hidden
          className={cn(
            "size-3.5 shrink-0",
            iconColor,
            isRunning ? "motion-safe:animate-spin" : "",
          )}
        />
      ) : (
        <span
          aria-hidden
          className="block size-1.5 shrink-0 rounded-full bg-fg-faint"
        />
      )}
      <span
        className={cn(
          "min-w-0 flex-1 truncate",
          isOk ? "text-fg-muted line-through decoration-fg-faint/40" : "text-fg",
          isSkipped ? "italic text-fg-faint" : "",
        )}
      >
        {step.label}
      </span>
      {typeof step.elapsedSeconds === "number" && step.elapsedSeconds > 0 ? (
        <span className="shrink-0 font-mono text-[10px] tabular-nums text-fg-faint">
          {formatElapsed(step.elapsedSeconds)}
        </span>
      ) : null}
    </li>
  );
}

export function CelebrationShimmer(): JSX.Element {
  return (
    <div
      aria-hidden
      className="pointer-events-none absolute inset-0 motion-safe:animate-[fadeIn_400ms_ease-out]"
    >
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top,var(--color-success)_0%,transparent_55%)] opacity-[0.08]" />
    </div>
  );
}

export function computeProgressPct(setup: SetupExperienceState): number {
  if (setup.phase === SetupStatus.Complete) return 100;
  if (
    setup.phase === SetupStatus.WarmingUp ||
    setup.phase === SetupStatus.Queued
  ) {
    return 6;
  }
  if (setup.summary.total > 0) {
    const fraction =
      (setup.summary.completed + setup.summary.running * 0.5) /
      setup.summary.total;
    return Math.max(6, Math.min(99, Math.round(fraction * 100)));
  }
  return 18;
}

export function formatElapsed(s: number): string {
  if (!Number.isFinite(s) || s <= 0) return "—";
  const total = Math.floor(s);
  if (total < 60) return `${total}s`;
  if (total < 3_600) {
    const m = Math.floor(total / 60);
    const r = total % 60;
    return `${m}m ${String(r).padStart(2, "0")}s`;
  }
  const h = Math.floor(total / 3_600);
  const m = Math.floor((total % 3_600) / 60);
  return `${h}h ${m}m`;
}
