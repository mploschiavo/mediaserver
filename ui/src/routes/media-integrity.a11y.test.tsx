import type { ComponentType } from "react";
import { describe, it, vi } from "vitest";
import { renderWithProviders } from "@/test/render";
import { assertNoA11yViolations } from "@/test/a11y";

// Mock the data hooks the route consumes. Following the pattern used
// in routes/ops.test.tsx: import the actual barrel for type-safety
// then override only the hooks the page reads. The route's mutations
// (reconcile / enforce / resolve) need to look like idle mutations
// so the buttons render in their default (non-pending) state.
const noopMutation = {
  mutate: vi.fn(),
  mutateAsync: vi.fn(async () => undefined),
  isPending: false,
  isError: false,
  isSuccess: false,
  isIdle: true,
  status: "idle" as const,
  data: undefined,
  error: null,
  reset: vi.fn(),
  variables: undefined,
  context: undefined,
  failureCount: 0,
  failureReason: null,
  submittedAt: 0,
};

vi.mock("@/api", async () => {
  const actual = await vi.importActual<typeof import("@/api")>("@/api");
  return {
    ...actual,
    useMediaIntegrityStatus: () => ({
      data: {
        last_enforce: { ts: new Date(0).toISOString(), detail: {} },
        last_reconcile: { ts: new Date(0).toISOString(), detail: {} },
        policy_version: 1,
        servarr_adapters: ["radarr", "sonarr"],
        bazarr_present: true,
        missing_api_keys: [],
      },
      isLoading: false,
      error: null,
    }),
    useMediaIntegrityProgress: () => ({
      data: { in_progress: false },
      isLoading: false,
      error: null,
    }),
    useReconcile: () => noopMutation,
    useEnforceConfig: () => noopMutation,
    useResolveReview: () => noopMutation,
  };
});

vi.mock("react-hotkeys-hook", () => ({
  useHotkeys: () => undefined,
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { Route as MediaIntegrityRoute } from "./media-integrity";

const MediaIntegrityPage = MediaIntegrityRoute.options
  .component as ComponentType;

describe("media-integrity route a11y", () => {
  it("renders with no serious or critical axe violations", async () => {
    const { container } = renderWithProviders(<MediaIntegrityPage />);
    await assertNoA11yViolations(container);
  });
});
