import { useMemo, useState, type JSX } from "react";
import { Link } from "@tanstack/react-router";
import {
  ArrowRight,
  Check,
  ChevronDown,
  ChevronRight,
  Sparkles,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  type OnboardingShape,
  type OnboardingStep,
} from "./hooks";
import {
  OnboardingProgressBar,
  StepRow,
  stepRoute,
} from "./onboardingChecklistParts";

interface GroupedSteps {
  actionable: readonly OnboardingStep[];
  done: readonly OnboardingStep[];
}

function groupSteps(data: OnboardingShape | undefined): GroupedSteps {
  if (!data || !Array.isArray(data.steps)) {
    return { actionable: [], done: [] };
  }
  const actionable: OnboardingStep[] = [];
  const done: OnboardingStep[] = [];
  for (const step of data.steps) {
    if (step.status === "ok") {
      done.push(step);
    } else {
      actionable.push(step);
    }
  }
  return { actionable, done };
}

interface OnboardingChecklistViewProps {
  data: OnboardingShape;
  /** Initial expanded/collapsed state of the "Done for you" group.
   *  Demos pass ``true`` so completed steps are visible by default. */
  initialShowCompleted?: boolean;
}

/**
 * Pure-presentational checklist. Use this directly to render
 * specific shapes (demos, Storybook, fixtures); the production
 * surface ``OnboardingChecklist`` wraps this with the live
 * ``useOnboarding()`` query.
 */
export function OnboardingChecklistView({
  data,
  initialShowCompleted = false,
}: OnboardingChecklistViewProps): JSX.Element | null {
  const [showCompleted, setShowCompleted] = useState(initialShowCompleted);
  const grouped = useMemo(() => groupSteps(data), [data]);

  if (data.total === 0) return null;

  const { actionable, done } = grouped;
  const allDone = actionable.length === 0;
  const progressPct = Math.max(0, Math.min(100, data.progress_pct));
  const firstActionable = actionable[0];
  const firstRoute = firstActionable ? stepRoute(firstActionable) : undefined;

  return (
    <Card
      data-testid="onboarding-checklist"
      className="relative overflow-hidden"
    >
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-accent/60 to-transparent"
      />
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2">
              <Sparkles aria-hidden className="size-4 text-accent" />
              {allDone
                ? "Your media stack is ready"
                : "Finish setting up your stack"}
            </CardTitle>
            <CardDescription>
              {allDone
                ? "Every checklist item is satisfied. You can keep customizing whenever you like."
                : `${data.completed} of ${data.total} ready · ${actionable.length} ${actionable.length === 1 ? "item" : "items"} need attention`}
            </CardDescription>
          </div>
          <div
            className="text-right tabular-nums"
            data-testid="onboarding-checklist-progress"
          >
            <div className="text-xl font-semibold text-fg">{progressPct}%</div>
            <div className="text-[10px] uppercase tracking-wide text-fg-faint">
              ready
            </div>
          </div>
        </div>
        <OnboardingProgressBar pct={progressPct} firstRun={data.is_first_run} />
      </CardHeader>
      <CardContent className="space-y-4">
        {actionable.length > 0 ? (
          <ul
            className="space-y-2"
            data-testid="onboarding-checklist-actionable"
          >
            {actionable.map((step) => (
              <StepRow key={step.id} step={step} prominent />
            ))}
          </ul>
        ) : null}

        {firstActionable ? (
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs text-fg-muted">
              Start with{" "}
              <span className="font-medium text-fg">{firstActionable.label}</span>
              .
            </span>
            {firstRoute ? (
              <Button
                variant="primary"
                asChild
                data-testid="onboarding-checklist-resume"
                className="gap-1"
              >
                <Link to={firstRoute}>
                  Resume setup
                  <ArrowRight aria-hidden className="size-3.5" />
                </Link>
              </Button>
            ) : (
              <Button
                variant="primary"
                disabled
                data-testid="onboarding-checklist-resume"
              >
                Resume setup
              </Button>
            )}
          </div>
        ) : null}

        {done.length > 0 ? (
          <div className="rounded-md border border-border/60 bg-bg-1/40">
            <button
              type="button"
              onClick={() => setShowCompleted((v) => !v)}
              className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-xs text-fg-muted hover:text-fg"
              aria-expanded={showCompleted}
              data-testid="onboarding-checklist-done-toggle"
            >
              <span className="flex items-center gap-2">
                <Check aria-hidden className="size-3.5 text-success" />
                <span>
                  Done for you ·{" "}
                  <span className="text-fg">
                    {done.length} {done.length === 1 ? "item" : "items"}
                  </span>
                </span>
              </span>
              {showCompleted ? (
                <ChevronDown aria-hidden className="size-3.5" />
              ) : (
                <ChevronRight aria-hidden className="size-3.5" />
              )}
            </button>
            {showCompleted ? (
              <ul
                className="space-y-1 border-t border-border/50 px-3 py-2"
                data-testid="onboarding-checklist-done-list"
              >
                {done.map((step) => (
                  <StepRow key={step.id} step={step} prominent={false} />
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
