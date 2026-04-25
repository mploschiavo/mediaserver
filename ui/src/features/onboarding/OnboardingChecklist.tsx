import { Link } from "@tanstack/react-router";
import { Check, Circle } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { asArray } from "@/lib/coerce";
import { useOnboarding, type OnboardingStep } from "./hooks";

/**
 * Read a step into its display label, regardless of whether the
 * payload emitted a string or a structured object.
 */
function stepLabel(step: OnboardingStep): string {
  if (typeof step === "string") return step;
  return step.label ?? step.title ?? step.id ?? "Step";
}

/** Read a step's deep-link route, if any. */
function stepRoute(step: OnboardingStep): string | undefined {
  if (typeof step === "string") return undefined;
  return step.route ?? step.href;
}

/** Read a step's optional description / sub-copy. */
function stepDescription(step: OnboardingStep): string | undefined {
  if (typeof step === "string") return undefined;
  return step.description;
}

/**
 * First-run onboarding checklist — sourced from the controller's
 * `/api/onboarding` payload. Renders nothing while the query is in
 * flight (so the home route doesn't flash a skeleton above the
 * media-integrity content) and bails out entirely if the wizard has
 * no remaining work.
 *
 * "Resume setup" deep-links to the first pending step's route via
 * the Tanstack <Link>.
 */
export function OnboardingChecklist() {
  const query = useOnboarding();

  if (query.isLoading) {
    return (
      <Card data-testid="onboarding-checklist-loading">
        <CardHeader>
          <Skeleton className="h-4 w-48" />
          <Skeleton className="h-3 w-64" />
        </CardHeader>
        <CardContent>
          <Skeleton className="h-16 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (query.error || !query.data) return null;

  const completed = asArray<OnboardingStep>(query.data.completed);
  const pending = asArray<OnboardingStep>(query.data.pending);

  // No work and nothing completed — nothing to render. Banner pattern:
  // this card disappears entirely once the wizard is irrelevant.
  if (completed.length === 0 && pending.length === 0) return null;

  const firstPendingRoute = (() => {
    for (const step of pending) {
      const route = stepRoute(step);
      if (route) return route;
    }
    return undefined;
  })();

  return (
    <Card data-testid="onboarding-checklist">
      <CardHeader>
        <CardTitle>Finish setting up your stack</CardTitle>
        <CardDescription>
          {pending.length === 0
            ? "Setup complete — every step has been ticked off."
            : `${pending.length} ${pending.length === 1 ? "step" : "steps"} remaining`}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {completed.length > 0 ? (
          <ul
            className="space-y-2"
            data-testid="onboarding-checklist-completed"
          >
            {completed.map((step, i) => (
              <li
                key={`completed-${i}`}
                className="flex items-start gap-2 text-sm text-fg-muted"
              >
                <Check
                  className="mt-0.5 size-4 shrink-0 text-success"
                  aria-hidden
                />
                <span className="line-through">{stepLabel(step)}</span>
              </li>
            ))}
          </ul>
        ) : null}

        {pending.length > 0 ? (
          <ul
            className="space-y-2"
            data-testid="onboarding-checklist-pending"
          >
            {pending.map((step, i) => {
              const desc = stepDescription(step);
              return (
                <li
                  key={`pending-${i}`}
                  className="flex items-start gap-2 rounded-md border border-[color-mix(in_oklab,var(--color-accent)_25%,transparent)] bg-[color-mix(in_oklab,var(--color-accent)_6%,transparent)] p-2 text-sm"
                >
                  <Circle
                    className="mt-0.5 size-4 shrink-0 text-accent"
                    aria-hidden
                  />
                  <div>
                    <div className="font-medium text-fg">
                      {stepLabel(step)}
                    </div>
                    {desc ? (
                      <div className="text-xs text-fg-muted">{desc}</div>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ul>
        ) : null}

        {pending.length > 0 ? (
          firstPendingRoute ? (
            <Button
              variant="primary"
              asChild
              data-testid="onboarding-checklist-resume"
            >
              <Link to={firstPendingRoute}>Resume setup</Link>
            </Button>
          ) : (
            <Button
              variant="primary"
              disabled
              data-testid="onboarding-checklist-resume"
            >
              Resume setup
            </Button>
          )
        ) : null}
      </CardContent>
    </Card>
  );
}

/**
 * Helper for the home-route to decide whether to mount the
 * checklist at all. Returns true iff the wizard has any completed
 * or pending steps to talk about.
 */
export function onboardingHasContent(
  data:
    | {
        completed?: readonly OnboardingStep[];
        pending?: readonly OnboardingStep[];
      }
    | undefined,
): boolean {
  if (!data) return false;
  const completed = asArray(data.completed);
  const pending = asArray(data.pending);
  return completed.length > 0 || pending.length > 0;
}
