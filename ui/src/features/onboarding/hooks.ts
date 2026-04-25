// Feature-local query hook for the onboarding wizard surface.
//
// Lives here rather than in `src/api/hooks.ts` because the parent
// barrel is owned by sibling agents shipping other waves in parallel.
// The onboarding endpoint is opaque (`additionalProperties: true` in
// OpenAPI) so we model only the canonical fields the UI needs and
// fall back gracefully when any of them is missing.

import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { fetcher } from "@/api/client";

const ONBOARDING_PATH = "api/onboarding";

/**
 * A single onboarding step. The controller may emit either a string
 * (label only) or a structured `{ id, label, route }` object — the
 * wizard renders both shapes by way of the helpers in
 * `OnboardingChecklist.tsx`.
 */
export type OnboardingStep =
  | string
  | {
      id?: string;
      label?: string;
      title?: string;
      route?: string;
      href?: string;
      description?: string;
    };

export interface OnboardingShape {
  /** Current step (id or label) the wizard is paused on, if any. */
  step?: string;
  completed?: readonly OnboardingStep[];
  pending?: readonly OnboardingStep[];
}

export const onboardingQueryKey = ["onboarding"] as const;

/**
 * `useOnboarding` — fetches `/api/onboarding`. Cache for 30 s; the
 * wizard state changes only when the operator finishes a step, and
 * mutations on those forms can invalidate the key directly.
 */
export function useOnboarding(): UseQueryResult<OnboardingShape> {
  return useQuery({
    queryKey: onboardingQueryKey,
    queryFn: () => fetcher<OnboardingShape>(ONBOARDING_PATH),
    staleTime: 30_000,
    retry: false,
  });
}
