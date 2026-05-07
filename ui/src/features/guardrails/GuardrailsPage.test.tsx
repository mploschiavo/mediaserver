import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

// Match the ops.test.tsx pattern: mock the feature hook directly
// rather than the underlying fetcher. The page reads through
// `useGuardrails`, and stubbing the hook lets each test own its
// resolved/error state without coupling to react-query timing.
const guardrailsState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const updateMutate = vi.hoisted(() => vi.fn());

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useGuardrails: () => guardrailsState,
    useUpdateGuardrailsConfig: () => ({
      mutate: updateMutate,
      isPending: false,
    }),
  };
});

import { GuardrailsPage } from "./GuardrailsPage";

beforeEach(() => {
  guardrailsState.data = undefined;
  guardrailsState.isLoading = false;
  guardrailsState.error = null;
  updateMutate.mockReset();
});

describe("GuardrailsPage", () => {
  it("renders one tab per domain and the storage tab is default", () => {
    // At least one rule is required — the page renders an empty state
    // (not the tabs) when guardrails.length === 0.
    guardrailsState.data = {
      guardrails: [
        {
          id: "storage:per_mount_threshold",
          domain: "storage",
          description: "Per-mount usage threshold",
          threshold: { max_percent: 85 },
        },
      ],
      evaluation_interval_seconds: 300,
    };
    renderWithProviders(<GuardrailsPage />);
    expect(screen.getByTestId("guardrails-tabs")).toBeInTheDocument();
    for (const id of [
      "storage", "bandwidth", "external_api", "media_quality",
      "job_health", "auth", "cost", "dependency",
    ]) {
      expect(
        screen.getByTestId(`guardrails-tab-${id}`),
      ).toBeInTheDocument();
    }
  });

  it("renders the error banner when the query fails", () => {
    guardrailsState.error = new Error("nope");
    renderWithProviders(<GuardrailsPage />);
    expect(screen.getByTestId("guardrails-error")).toBeInTheDocument();
  });
});
