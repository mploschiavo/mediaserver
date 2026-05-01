import { useState, type JSX } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";
import {
  type SetupExperienceState,
} from "./setupState";
import { SetupStatus } from "./setupStatusConstants";
import {
  CelebrationShimmer,
  HeroIcon,
  ProgressBar,
  computeProgressPct,
  formatElapsed,
} from "./bootstrapBannerParts";
import {
  BannerCtas,
  BannerEyebrow,
  BannerMetaBox,
  BannerSummary,
  BannerTimeline,
} from "./bootstrapBannerSections";

const TIMELINE_PREVIEW_COUNT = 4;

const TONE_RING: Record<SetupExperienceState["statusTone"], string> = {
  info: "ring-info/30 shadow-[0_0_48px_-20px_var(--color-info)]",
  success: "ring-success/40 shadow-[0_0_56px_-18px_var(--color-success)]",
  warning: "ring-warning/40 shadow-[0_0_48px_-20px_var(--color-warning)]",
  danger: "ring-danger/40 shadow-[0_0_48px_-20px_var(--color-danger)]",
};

const TONE_GRADIENT: Record<SetupExperienceState["statusTone"], string> = {
  info: "from-info/15 via-bg-1 to-bg-1",
  success: "from-success/20 via-bg-1 to-bg-1",
  warning: "from-warning/20 via-bg-1 to-bg-1",
  danger: "from-danger/20 via-bg-1 to-bg-1",
};

const TONE_RAIL: Record<SetupExperienceState["statusTone"], string> = {
  info: "via-info/70",
  success: "via-success/70",
  warning: "via-warning/70",
  danger: "via-danger/70",
};

interface BootstrapProgressBannerViewProps {
  setup: SetupExperienceState;
  /** Whether the user has dismissed the banner. View hides itself when true. */
  dismissed?: boolean;
  /** Whether the celebration window has elapsed. View hides itself when true. */
  celebratedHidden?: boolean;
  /** Operator clicked the X — controlled by a parent wrapper, no-op in demos. */
  onDismiss?: () => void;
  /** Operator clicked Retry. No-op in demos. */
  onRetry?: () => void;
  /** Disable retry button while a retry is in flight. */
  retryDisabled?: boolean;
}

/**
 * Pure-presentational hero card. All data flow comes through the
 * ``setup`` prop; dismiss/retry are callbacks the wrapper supplies.
 *
 * Use this directly when you want full control over the rendered
 * state — demo routes, Storybook stories, or any caller that wants
 * to bypass the live ``/status`` query. For the production
 * dashboard surface, use ``BootstrapProgressBanner``.
 */
export function BootstrapProgressBannerView({
  setup,
  dismissed = false,
  celebratedHidden = false,
  onDismiss,
  onRetry,
  retryDisabled = false,
}: BootstrapProgressBannerViewProps): JSX.Element | null {
  const [showAllSteps, setShowAllSteps] = useState(false);

  const critical =
    setup.phase === SetupStatus.Failed ||
    setup.phase === SetupStatus.Cancelled ||
    setup.phase === SetupStatus.TimedOut ||
    setup.phase === SetupStatus.CompleteWithWarnings;

  if (!setup.isVisible) return null;
  if (setup.phase === SetupStatus.Complete && celebratedHidden) return null;
  if (dismissed && !critical) return null;

  const progressPct = computeProgressPct(setup);
  const elapsedDisplay = formatElapsed(setup.elapsedSeconds);
  const tone = setup.statusTone;
  const visibleTimeline = showAllSteps
    ? setup.timeline
    : setup.timeline.slice(0, TIMELINE_PREVIEW_COUNT);
  const overflow = setup.timeline.length - visibleTimeline.length;

  return (
    <section
      role="status"
      aria-live="polite"
      data-testid="bootstrap-progress-banner"
      data-phase={setup.phase}
      className={cn(
        "relative overflow-hidden rounded-2xl border border-border/60 bg-gradient-to-br p-6 ring-1 transition-all duration-500",
        TONE_GRADIENT[tone],
        TONE_RING[tone],
      )}
    >
      <div
        aria-hidden
        className={cn(
          "pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent to-transparent",
          TONE_RAIL[tone],
        )}
      />

      <div className="flex flex-wrap items-start gap-4">
        <HeroIcon phase={setup.phase} tone={tone} />

        <div className="flex min-w-0 flex-1 flex-col gap-3">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <BannerEyebrow title={setup.title} description={setup.description} />
            <div className="flex items-start gap-2">
              <BannerMetaBox elapsedDisplay={elapsedDisplay} />
              {!critical && setup.phase !== SetupStatus.WarmingUp && onDismiss ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label="Dismiss bootstrap banner"
                  title="Dismiss — only do this if you're confident the stack is up"
                  onClick={onDismiss}
                  data-testid="bootstrap-progress-banner-dismiss"
                >
                  <X aria-hidden className="size-3.5" />
                </Button>
              ) : null}
            </div>
          </div>

          {setup.phase !== SetupStatus.Complete && setup.phase !== SetupStatus.WarmingUp ? (
            <ProgressBar pct={progressPct} tone={tone} />
          ) : null}

          <BannerSummary summary={setup.summary} />
          <BannerTimeline
            steps={visibleTimeline}
            showAll={showAllSteps}
            overflow={overflow}
            onToggle={() => setShowAllSteps((v) => !v)}
          />
          <BannerCtas
            ctas={setup.ctas}
            phase={setup.phase}
            onRetry={onRetry ?? (() => undefined)}
            retryDisabled={retryDisabled}
          />
        </div>
      </div>

      {setup.phase === SetupStatus.Complete ? <CelebrationShimmer /> : null}
    </section>
  );
}
