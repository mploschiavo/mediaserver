import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

// Stub the router Link so we can render in isolation without the
// router provider — the test only cares about the visible label.
vi.mock("@tanstack/react-router", () => ({
  Link: ({
    children,
    to: _to,
    ...rest
  }: {
    children: React.ReactNode;
    to?: string;
  } & Record<string, unknown>) => <a {...rest}>{children}</a>,
}));

import { StorageTransitionFeed } from "./StorageTransitionFeed";

describe("StorageTransitionFeed", () => {
  it("renders the empty caption when there are no transitions", () => {
    renderWithProviders(<StorageTransitionFeed transitions={[]} />);
    expect(
      screen.getByTestId("storage-transition-feed-empty"),
    ).toBeInTheDocument();
    // Empty-state visibility — the card itself remains visible.
    expect(
      screen.getByTestId("storage-transition-feed"),
    ).toBeInTheDocument();
  });

  it("sorts rows newest-first and tones engaged/released distinctly", () => {
    renderWithProviders(
      <StorageTransitionFeed
        transitions={[
          {
            ts: 100,
            action: "disk_guardrail_lockdown_engaged",
            actor: "operator:matthew",
            used_percent: 78.5,
          },
          {
            ts: 200,
            action: "disk_guardrail_lockdown_released",
            actor: "auto",
            used_percent: 59.2,
          },
        ]}
      />,
    );
    // Row 0 should be the released (newer ts).
    const action0 = screen.getByTestId("storage-transition-action-0");
    expect(action0.textContent).toMatch(/released/);
    expect(action0).toHaveAttribute("data-tone", "success");
    const action1 = screen.getByTestId("storage-transition-action-1");
    expect(action1.textContent).toMatch(/engaged/);
    expect(action1).toHaveAttribute("data-tone", "warning");
  });

  it("links to the audit log", () => {
    renderWithProviders(<StorageTransitionFeed transitions={[]} />);
    const link = screen.getByTestId("storage-transition-feed-show-all");
    expect(link).toBeInTheDocument();
  });
});
