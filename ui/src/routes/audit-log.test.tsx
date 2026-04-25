import type { ComponentType } from "react";
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

// Mock the data hooks so the smoke render is deterministic. Both
// the AuditLogTable and the IntegrityBanner read through the
// feature's hook module, so we stub there.
vi.mock("@/features/audit-log/hooks", () => ({
  useAuditLog: () => ({
    data: { entries: [] },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
  useAuditLogHead: () => ({
    data: { height: 0, hash: "" },
    isLoading: false,
    error: null,
  }),
  useAuditLogVerify: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
}));

// AuditLogTable imports useAuditLog from `@/api/hooks` directly to
// avoid pulling the feature barrel into the shared API surface.
// Mock that path too so the route smoke test stays isolated.
vi.mock("@/api/hooks", async () => {
  const actual = await vi.importActual<typeof import("@/api/hooks")>(
    "@/api/hooks",
  );
  return {
    ...actual,
    useAuditLog: () => ({
      data: { entries: [] },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    }),
  };
});

// AuditLogTable now reads ?action=... from `useLocation()` so it can
// pre-fill the filter from a deep-link. The route smoke test mounts
// the table outside a router, so stub the hook with a static empty
// search object. We import the actual module for everything else
// (createRoute, etc.) so the route registration still works.
vi.mock("@tanstack/react-router", async () => {
  const actual = await vi.importActual<typeof import("@tanstack/react-router")>(
    "@tanstack/react-router",
  );
  return {
    ...actual,
    useLocation: () => ({ pathname: "/audit-log", search: {} }),
  };
});

import { AuditLogRoute } from "./audit-log";

const AuditLogPage = AuditLogRoute.options.component as ComponentType;

describe("audit-log route", () => {
  it("renders the page header with title + description", () => {
    renderWithProviders(<AuditLogPage />);
    expect(
      screen.getByRole("heading", { name: /Audit log/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Tamper-evident record of every operator action/i),
    ).toBeInTheDocument();
  });

  it("mounts the integrity banner and the audit log table", () => {
    renderWithProviders(<AuditLogPage />);
    expect(screen.getByTestId("integrity-banner")).toBeInTheDocument();
    expect(screen.getByTestId("audit-log-table")).toBeInTheDocument();
  });

  it("registers the route at /audit-log", () => {
    expect((AuditLogRoute.options as unknown as { path: string }).path).toBe("/audit-log");
  });
});
