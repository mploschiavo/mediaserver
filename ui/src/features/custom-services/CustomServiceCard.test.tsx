import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const defineMutate = vi.hoisted(() => vi.fn());

vi.mock("./hooks", () => ({
  useDefineCustomService: () => ({
    mutate: defineMutate,
    isPending: false,
  }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { CustomServiceCard } from "./CustomServiceCard";

describe("CustomServiceCard", () => {
  beforeEach(() => {
    defineMutate.mockReset();
  });

  it("renders the card header and empty state", () => {
    renderWithProviders(<CustomServiceCard />);
    expect(screen.getByTestId("custom-services-card")).toBeInTheDocument();
    // CardTitle + EmptyState description both surface "Custom services";
    // assert at least one match rather than expecting a single one.
    expect(screen.getAllByText(/Custom services/i).length).toBeGreaterThan(0);
    expect(
      screen.getByText(/No custom services defined/i),
    ).toBeInTheDocument();
  });

  it("opens the define dialog when the trigger is clicked", async () => {
    renderWithProviders(<CustomServiceCard />);
    await userEvent.click(
      screen.getByTestId("custom-service-define-trigger"),
    );
    await waitFor(() =>
      expect(
        screen.getByTestId("custom-service-dialog"),
      ).toBeInTheDocument(),
    );
  });
});
