import type { ComponentType } from "react";
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

vi.mock("@/features/sessions/SessionsTable", () => ({
  SessionsTable: () => <div data-testid="sessions-table-stub" />,
}));

import { Route as SessionsRoute } from "./sessions";

const SessionsPage = SessionsRoute.options.component as ComponentType;

describe("sessions route", () => {
  it("renders the page header and mounts the SessionsTable", () => {
    renderWithProviders(<SessionsPage />);
    expect(
      screen.getByRole("heading", { name: /Active sessions/i }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("sessions-table-stub")).toBeInTheDocument();
  });
});
