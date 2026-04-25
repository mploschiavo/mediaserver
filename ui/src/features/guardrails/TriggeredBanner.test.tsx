import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const fetcherMock = vi.hoisted(() => vi.fn());
vi.mock("@/api/client", () => ({
  fetcher: fetcherMock,
  getBaseUrl: () => "",
}));

import { TriggeredBanner } from "./TriggeredBanner";

describe("TriggeredBanner", () => {
  it("renders nothing when no rule is firing", async () => {
    fetcherMock.mockResolvedValue({
      guardrails: [
        { id: "x", domain: "storage", description: "", threshold: {}, last_status: "ok" },
      ],
    });
    const { container } = renderWithProviders(<TriggeredBanner />);
    await waitFor(() => {
      expect(
        container.querySelector('[data-testid="guardrails-triggered-banner"]'),
      ).toBeNull();
    });
  });

  it("renders the banner with worst-severity rule when warning fires", async () => {
    fetcherMock.mockResolvedValue({
      guardrails: [
        {
          id: "storage:free_space_floor",
          domain: "storage",
          description: "",
          threshold: {},
          last_status: "warning",
          last_triggered_at: 100,
        },
        {
          id: "auth:failed_login_spike",
          domain: "auth",
          description: "",
          threshold: {},
          last_status: "critical",
          last_triggered_at: 50,
        },
      ],
    });
    renderWithProviders(<TriggeredBanner />);
    await waitFor(() => {
      expect(
        screen.getByTestId("guardrails-triggered-banner"),
      ).toBeInTheDocument();
    });
    // Critical is "worst", so the banner targets that rule.
    expect(screen.getByText(/auth:failed_login_spike/)).toBeInTheDocument();
  });

  it("treats disabled rules as silent", async () => {
    fetcherMock.mockResolvedValue({
      guardrails: [
        {
          id: "x",
          domain: "storage",
          description: "",
          threshold: {},
          last_status: "critical",
          disabled: true,
        },
      ],
    });
    const { container } = renderWithProviders(<TriggeredBanner />);
    await waitFor(() => {
      expect(
        container.querySelector('[data-testid="guardrails-triggered-banner"]'),
      ).toBeNull();
    });
  });
});
