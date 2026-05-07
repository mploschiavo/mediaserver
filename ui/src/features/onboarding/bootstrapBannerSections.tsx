import type { JSX } from "react";
import { Link } from "@tanstack/react-router";
import {
  ArrowRight,
  ChevronDown,
  ChevronRight,
  Sparkles,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import type {
  SetupCta,
  SetupExperienceState,
  TimelineStep,
} from "./setupState";
import { TimelineRow } from "./bootstrapBannerParts";
import { SetupStatus } from "./setupStatusConstants";

export function BannerEyebrow({
  title,
  description,
}: {
  title: string;
  description: string;
}): JSX.Element {
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.18em] text-fg-faint">
        <Sparkles aria-hidden className="size-3" />
        <span>First-run experience</span>
      </div>
      <h2
        className="mt-1 text-xl font-semibold text-fg sm:text-2xl"
        data-testid="bootstrap-progress-banner-title"
      >
        {title}
      </h2>
      <p
        className="mt-1 text-sm text-fg-muted"
        data-testid="bootstrap-progress-banner-description"
      >
        {description}
      </p>
    </div>
  );
}

export function BannerMetaBox({
  elapsedDisplay,
}: {
  elapsedDisplay: string;
}): JSX.Element {
  return (
    <div
      className="rounded-md bg-bg-1/70 px-3 py-1.5 text-right tabular-nums backdrop-blur"
      data-testid="bootstrap-progress-banner-meta"
    >
      <div className="text-[10px] uppercase tracking-wide text-fg-faint">
        Elapsed
      </div>
      <div
        className="font-mono text-xs text-fg"
        data-testid="bootstrap-progress-banner-elapsed"
      >
        {elapsedDisplay}
      </div>
    </div>
  );
}

export function BannerSummary({
  summary,
}: {
  summary: SetupExperienceState["summary"];
}): JSX.Element | null {
  if (summary.total <= 0) return null;
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] tabular-nums text-fg-faint">
      <span>
        <span className="text-fg">{summary.completed}</span> done
      </span>
      <span>
        <span className="text-info">{summary.running}</span> running
      </span>
      {summary.failed > 0 ? (
        <span>
          <span className="text-danger">{summary.failed}</span> failed
        </span>
      ) : null}
      {summary.skipped > 0 ? (
        <span>
          <span className="text-fg-muted">{summary.skipped}</span> skipped
        </span>
      ) : null}
      <span className="text-fg-faint">of {summary.total} steps</span>
    </div>
  );
}

export function BannerTimeline({
  steps,
  showAll,
  overflow,
  onToggle,
}: {
  steps: readonly TimelineStep[];
  showAll: boolean;
  overflow: number;
  onToggle: () => void;
}): JSX.Element | null {
  if (steps.length === 0) return null;
  return (
    <div
      className="rounded-lg border border-border/50 bg-bg-1/40 backdrop-blur-sm"
      data-testid="bootstrap-progress-banner-timeline"
    >
      <ul className="divide-y divide-border/40">
        {steps.map((step) => (
          <TimelineRow key={step.id} step={step} />
        ))}
      </ul>
      {overflow > 0 || showAll ? (
        <button
          type="button"
          onClick={onToggle}
          className="flex w-full items-center justify-between gap-2 px-3 py-2 text-xs text-fg-muted transition-colors hover:bg-bg-1 hover:text-fg"
          aria-expanded={showAll}
          data-testid="bootstrap-progress-banner-timeline-toggle"
        >
          <span>
            {showAll
              ? "Show fewer steps"
              : `Show ${overflow} more step${overflow === 1 ? "" : "s"}`}
          </span>
          {showAll ? (
            <ChevronDown aria-hidden className="size-3.5" />
          ) : (
            <ChevronRight aria-hidden className="size-3.5" />
          )}
        </button>
      ) : null}
    </div>
  );
}

export function BannerCtas({
  ctas,
  phase,
  onRetry,
  retryDisabled,
  onClose,
}: {
  ctas: readonly SetupCta[];
  phase: SetupExperienceState["phase"];
  onRetry: () => void;
  retryDisabled: boolean;
  /**
   * Wrapper passes this when the current phase warrants a labeled
   * Close button (Complete-state acknowledgement). Renders trailing
   * the action CTAs as a ghost-variant button so it reads as
   * secondary to the primary "Open apps" / "Verify health" actions.
   */
  onClose?: () => void;
}): JSX.Element {
  return (
    <div className="flex flex-wrap items-center gap-2 pt-1">
      {ctas.map((cta) => {
        if (cta.href) {
          const isPrimary = phase === SetupStatus.Complete && cta.key === "open_apps";
          return (
            <Button
              key={cta.key}
              asChild
              size="sm"
              variant={isPrimary ? "primary" : "outline"}
              className="gap-1"
            >
              <Link to={cta.href}>
                {cta.label}
                {isPrimary ? (
                  <ArrowRight aria-hidden className="size-3.5" />
                ) : null}
              </Link>
            </Button>
          );
        }
        return (
          <Button
            key={cta.key}
            size="sm"
            variant={cta.key === "retry" ? "primary" : "outline"}
            onClick={() => {
              if (cta.actionName !== "bootstrap") return;
              onRetry();
            }}
            disabled={retryDisabled}
          >
            {cta.label}
          </Button>
        );
      })}
      {onClose ? (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={onClose}
          data-testid="bootstrap-progress-banner-close"
        >
          Close
        </Button>
      ) : null}
    </div>
  );
}
