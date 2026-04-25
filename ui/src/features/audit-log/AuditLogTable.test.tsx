import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const auditLogState = vi.hoisted(() => ({
  data: undefined as { entries: unknown[] } | undefined,
  isLoading: false,
  error: null as Error | null,
  refetch: vi.fn(),
}));

const auditLogCalls = vi.hoisted(() => [] as Array<unknown>);
const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());

vi.mock("@/api/hooks", async () => {
  const actual = await vi.importActual<typeof import("@/api/hooks")>(
    "@/api/hooks",
  );
  return {
    ...actual,
    useAuditLog: (opts?: unknown) => {
      auditLogCalls.push(opts);
      return auditLogState;
    },
  };
});

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

const locationState = vi.hoisted(() => ({
  pathname: "/audit-log",
  search: {} as Record<string, unknown>,
}));

vi.mock("@tanstack/react-router", async () => {
  const actual = await vi.importActual<typeof import("@tanstack/react-router")>(
    "@tanstack/react-router",
  );
  return {
    ...actual,
    useLocation: () => locationState,
  };
});

import { AuditLogTable } from "./AuditLogTable";

describe("AuditLogTable", () => {
  beforeEach(() => {
    auditLogState.data = undefined;
    auditLogState.isLoading = false;
    auditLogState.error = null;
    auditLogState.refetch.mockReset();
    auditLogCalls.length = 0;
    toastSuccess.mockReset();
    toastError.mockReset();
    locationState.pathname = "/audit-log";
    locationState.search = {};
  });
  afterEach(() => {
    auditLogCalls.length = 0;
  });

  it("renders the skeleton table while loading", () => {
    auditLogState.isLoading = true;
    renderWithProviders(<AuditLogTable />);
    expect(screen.getByTestId("audit-log-loading")).toBeInTheDocument();
  });

  it("renders the empty state when entries=[]", () => {
    auditLogState.data = { entries: [] };
    renderWithProviders(<AuditLogTable />);
    expect(screen.getByText(/No audit entries yet/i)).toBeInTheDocument();
  });

  it("renders an error card with retry when the query fails", () => {
    auditLogState.error = new Error("boom");
    renderWithProviders(<AuditLogTable />);
    expect(screen.getByTestId("audit-log-error")).toHaveTextContent("boom");
    fireEvent.click(screen.getByTestId("audit-log-retry"));
    expect(auditLogState.refetch).toHaveBeenCalled();
  });

  it("renders one row per entry with actor, action, and result badge", () => {
    auditLogState.data = {
      entries: [
        {
          ts: new Date(Date.now() - 60_000).toISOString(),
          actor: "matt",
          action: "session.revoke",
          target: "user:alice",
          result: "ok",
          idempotency_key: "abc-123-def-456",
        },
        {
          ts: new Date(Date.now() - 5 * 60_000).toISOString(),
          actor: "alice",
          action: "user.create",
          target: "user:bob",
          result: "fail",
        },
      ],
    };
    renderWithProviders(<AuditLogTable />);
    // Both desktop + mobile branches mount, so values appear at least
    // once each.
    expect(screen.getAllByText("matt").length).toBeGreaterThan(0);
    expect(screen.getAllByText("alice").length).toBeGreaterThan(0);
    expect(screen.getAllByText("session.revoke").length).toBeGreaterThan(0);
    expect(screen.getAllByText("user.create").length).toBeGreaterThan(0);
    // Result badges render on both views.
    expect(screen.getAllByText("ok").length).toBeGreaterThan(0);
    expect(screen.getAllByText("fail").length).toBeGreaterThan(0);
  });

  it("threads the action filter to useAuditLog when typed", async () => {
    auditLogState.data = { entries: [] };
    renderWithProviders(<AuditLogTable />);
    const filter = screen.getByTestId("audit-log-filter");
    await userEvent.type(filter, "login");
    await waitFor(() => {
      const last = auditLogCalls[auditLogCalls.length - 1] as
        | { action?: string; limit?: number }
        | undefined;
      expect(last?.action).toBe("login");
    });
  });

  it("does not pass an action filter when the input is empty/whitespace", async () => {
    auditLogState.data = { entries: [] };
    renderWithProviders(<AuditLogTable />);
    // Type whitespace; trim() should drop it.
    const filter = screen.getByTestId("audit-log-filter");
    await userEvent.type(filter, "   ");
    const last = auditLogCalls[auditLogCalls.length - 1] as
      | { action?: string; limit?: number }
      | undefined;
    expect(last?.action).toBeUndefined();
    expect(last?.limit).toBe(50);
  });

  it("passes the chosen limit through useAuditLog when changed", async () => {
    auditLogState.data = { entries: [] };
    renderWithProviders(<AuditLogTable />);
    // Initial render uses limit=50.
    const initial = auditLogCalls[0] as { limit?: number } | undefined;
    expect(initial?.limit).toBe(50);

    // Radix Select: open via click on the trigger, then click the
    // listbox option. Mirrors the pattern used in select.test.tsx.
    await userEvent.click(screen.getByTestId("audit-log-limit-trigger"));
    await userEvent.click(
      await screen.findByRole("option", { name: /200 rows/i }),
    );
    await waitFor(() => {
      const last = auditLogCalls[auditLogCalls.length - 1] as
        | { limit?: number }
        | undefined;
      expect(last?.limit).toBe(200);
    });
  });

  it("renders an empty-state with the filter phrase when filter yields nothing", async () => {
    auditLogState.data = { entries: [] };
    renderWithProviders(<AuditLogTable />);
    await userEvent.type(screen.getByTestId("audit-log-filter"), "ghost");
    await waitFor(() => {
      expect(screen.getByText(/No matching entries/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/"ghost"/)).toBeInTheDocument();
  });

  it("pre-fills the action filter from the URL ?action= query param", async () => {
    locationState.search = { action: "job:scan-completed-downloads" };
    auditLogState.data = { entries: [] };
    renderWithProviders(<AuditLogTable />);
    const filter = screen.getByTestId("audit-log-filter") as HTMLInputElement;
    expect(filter.value).toBe("job:scan-completed-downloads");
    // And it threads through to useAuditLog.
    await waitFor(() => {
      const last = auditLogCalls[auditLogCalls.length - 1] as
        | { action?: string }
        | undefined;
      expect(last?.action).toBe("job:scan-completed-downloads");
    });
  });

  it("copies the idempotency key when the key chip is clicked", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
      writable: true,
    });
    auditLogState.data = {
      entries: [
        {
          ts: new Date().toISOString(),
          actor: "matt",
          action: "x.y",
          target: "t",
          result: "ok",
          idempotency_key: "key-abc-1234",
        },
      ],
    };
    renderWithProviders(<AuditLogTable />);
    const chips = screen.getAllByTestId("audit-log-copy");
    fireEvent.click(chips[0]!);
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("key-abc-1234"));
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
  });
});
