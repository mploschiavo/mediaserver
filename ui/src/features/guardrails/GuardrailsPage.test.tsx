import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const fetcherMock = vi.hoisted(() => vi.fn());
vi.mock("@/api/client", () => ({
  fetcher: fetcherMock,
  getBaseUrl: () => "",
}));

import { GuardrailsPage } from "./GuardrailsPage";

describe("GuardrailsPage", () => {
  it("renders one tab per domain and the storage tab is default", async () => {
    fetcherMock.mockResolvedValue({ guardrails: [] });
    renderWithProviders(<GuardrailsPage />);
    await waitFor(() => {
      expect(screen.getByTestId("guardrails-tabs")).toBeInTheDocument();
    });
    for (const id of [
      "storage", "bandwidth", "external_api", "media_quality",
      "job_health", "auth", "cost", "dependency",
    ]) {
      expect(
        screen.getByTestId(`guardrails-tab-${id}`),
      ).toBeInTheDocument();
    }
  });

  it("renders the error banner when the query fails", async () => {
    fetcherMock.mockRejectedValue(new Error("nope"));
    renderWithProviders(<GuardrailsPage />);
    await waitFor(() => {
      expect(screen.getByTestId("guardrails-error")).toBeInTheDocument();
    });
  });
});
