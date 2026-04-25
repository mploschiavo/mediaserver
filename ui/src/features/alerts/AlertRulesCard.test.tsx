import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

import { ALERT_RULES_STORAGE_KEY } from "./hooks";

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
}));

import { AlertRulesCard } from "./AlertRulesCard";

beforeEach(() => {
  window.localStorage.clear();
  vi.clearAllMocks();
});

describe("AlertRulesCard", () => {
  it("shows the client-side disclaimer banner", () => {
    renderWithProviders(<AlertRulesCard />);
    const banner = screen.getByTestId("alert-rules-disclaimer");
    expect(banner.textContent).toMatch(/run in your browser only/i);
    expect(banner.textContent).toMatch(/localStorage/);
  });

  it("renders the empty state when no rules exist", () => {
    renderWithProviders(<AlertRulesCard />);
    expect(screen.getByText(/no alert rules yet/i)).toBeInTheDocument();
  });

  it("opens the Add rule dialog and saves a new rule to localStorage", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AlertRulesCard />);
    await user.click(screen.getByTestId("alert-rules-add-trigger"));
    const dialog = await screen.findByTestId("alert-rules-add-dialog");
    await user.type(within(dialog).getByTestId("alert-rule-name"), "Sonarr");
    const svc = within(dialog).getByTestId("alert-rule-service") as HTMLInputElement;
    await user.clear(svc);
    await user.type(svc, "sonarr");
    const th = within(dialog).getByTestId(
      "alert-rule-threshold",
    ) as HTMLInputElement;
    await user.clear(th);
    await user.type(th, "5");
    await user.click(within(dialog).getByTestId("alert-rule-submit"));

    await waitFor(() => {
      const raw = window.localStorage.getItem(ALERT_RULES_STORAGE_KEY);
      expect(raw).not.toBeNull();
      const parsed = JSON.parse(raw ?? "[]");
      expect(parsed).toHaveLength(1);
      expect(parsed[0].name).toBe("Sonarr");
      expect(parsed[0].threshold).toBe(5);
    });
  });

  it("rejects an empty rule name with a toast", async () => {
    const { toast } = await import("sonner");
    const user = userEvent.setup();
    renderWithProviders(<AlertRulesCard />);
    await user.click(screen.getByTestId("alert-rules-add-trigger"));
    const dialog = await screen.findByTestId("alert-rules-add-dialog");
    await user.click(within(dialog).getByTestId("alert-rule-submit"));
    expect(toast.error).toHaveBeenCalledWith("Rule name is required");
  });

  it("renders existing rules and supports per-row delete", async () => {
    window.localStorage.setItem(
      ALERT_RULES_STORAGE_KEY,
      JSON.stringify([
        {
          id: "rule-x",
          name: "Radarr down",
          service: "radarr",
          condition: "down",
          threshold: 2,
          action: "toast",
        },
      ]),
    );
    const user = userEvent.setup();
    renderWithProviders(<AlertRulesCard />);
    expect(screen.getAllByText(/Radarr down/i).length).toBeGreaterThan(0);
    const deleteBtn = screen.getAllByTestId(/^alert-rule-delete-rule-x/)[0]!;
    await user.click(deleteBtn);
    await waitFor(() => {
      const raw = window.localStorage.getItem(ALERT_RULES_STORAGE_KEY);
      expect(JSON.parse(raw ?? "[]")).toEqual([]);
    });
  });
});
