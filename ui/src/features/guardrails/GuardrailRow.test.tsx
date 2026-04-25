import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const fetcherMock = vi.hoisted(() => vi.fn());
vi.mock("@/api/client", () => ({
  fetcher: fetcherMock,
  getBaseUrl: () => "",
}));

import { GuardrailRow } from "./GuardrailRow";
import type { Guardrail } from "./hooks";

const SAMPLE: Guardrail = {
  id: "storage:per_mount_threshold",
  domain: "storage",
  description: "Per-mount used percent",
  threshold: { max_percent: 85, target_percent: 75 },
  default_threshold: { max_percent: 85, target_percent: 75 },
  last_status: "warning",
  last_severity: "warning",
  last_severity_streak: 2,
  last_evaluated_at: 0,
  last_triggered_at: 0,
  disabled: false,
};

describe("GuardrailRow", () => {
  it("renders id, description, and status badge", () => {
    renderWithProviders(<GuardrailRow rule={SAMPLE} />);
    expect(screen.getByText("storage:per_mount_threshold")).toBeInTheDocument();
    expect(screen.getByText("Per-mount used percent")).toBeInTheDocument();
    expect(
      screen.getByTestId("guardrail-row-storage:per_mount_threshold-status"),
    ).toHaveTextContent("Warning");
  });

  it("renders one input per primitive threshold key", () => {
    renderWithProviders(<GuardrailRow rule={SAMPLE} />);
    expect(
      screen.getByTestId(
        "guardrail-input-storage:per_mount_threshold-max_percent",
      ),
    ).toHaveValue("85");
    expect(
      screen.getByTestId(
        "guardrail-input-storage:per_mount_threshold-target_percent",
      ),
    ).toHaveValue("75");
  });

  it("disables Save until a value changes", async () => {
    const user = userEvent.setup();
    renderWithProviders(<GuardrailRow rule={SAMPLE} />);
    const save = screen.getByTestId(
      "guardrail-save-storage:per_mount_threshold",
    ) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    await user.clear(
      screen.getByTestId(
        "guardrail-input-storage:per_mount_threshold-max_percent",
      ),
    );
    await user.type(
      screen.getByTestId(
        "guardrail-input-storage:per_mount_threshold-max_percent",
      ),
      "90",
    );
    expect(save.disabled).toBe(false);
  });

  it("renders 'Disabled' status when rule is disabled", () => {
    renderWithProviders(
      <GuardrailRow rule={{ ...SAMPLE, disabled: true }} />,
    );
    expect(
      screen.getByTestId("guardrail-row-storage:per_mount_threshold-status"),
    ).toHaveTextContent("Disabled");
  });

  it("highlights the focused row", () => {
    renderWithProviders(<GuardrailRow rule={SAMPLE} focused />);
    const row = screen.getByTestId(
      "guardrail-row-storage:per_mount_threshold",
    );
    expect(row.className).toContain("ring-2");
  });
});
