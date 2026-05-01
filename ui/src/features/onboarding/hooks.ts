// Feature-local query hook for the onboarding wizard surface.
//
// Lives here rather than in `src/api/hooks.ts` because the parent
// barrel is owned by sibling agents shipping other waves in parallel.

import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { fetcher } from "@/api/client";

const ONBOARDING_PATH = "api/onboarding";

const FIRST_RUN_STALE_MS = 5_000;
const SETTLED_STALE_MS = 60_000;

export type OnboardingStepStatus = "ok" | "warn" | "pending" | "error";

/**
 * Single auto-tracked checklist step emitted by `/api/onboarding`.
 * The controller derives `status` from live probes (service health,
 * api-key discovery, library config, routing config, download-client
 * bindings) so the UI never has to ask the operator to confirm work
 * the bootstrap job already finished.
 */
export interface OnboardingStep {
  id: string;
  label: string;
  status: OnboardingStepStatus;
  detail: string;
}

export interface OnboardingShape {
  steps: readonly OnboardingStep[];
  completed: number;
  total: number;
  progress_pct: number;
  is_first_run: boolean;
}

export const onboardingQueryKey = ["onboarding"] as const;

/**
 * Default route per step id. The controller emits step ids only;
 * UX wiring for "where the operator goes to satisfy this step"
 * lives in the UI. Lifting this onto the contract is a follow-up.
 */
export const ONBOARDING_STEP_ROUTES: Readonly<Record<string, string>> = {
  services_running: "/ops",
  api_keys: "/services",
  libraries: "/content",
  routing: "/routing",
  download_clients: "/services",
  bootstrap: "/jobs",
};

export function useOnboarding(): UseQueryResult<OnboardingShape> {
  return useQuery({
    queryKey: onboardingQueryKey,
    queryFn: () => fetcher<OnboardingShape>(ONBOARDING_PATH),
    staleTime: FIRST_RUN_STALE_MS,
    refetchInterval: (query) => {
      const data = query.state.data as OnboardingShape | undefined;
      if (!data) return FIRST_RUN_STALE_MS;
      return data.is_first_run ? FIRST_RUN_STALE_MS : SETTLED_STALE_MS;
    },
    retry: false,
  });
}
