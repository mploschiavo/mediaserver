import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";
import { EmergencyRevokeCard } from "./EmergencyRevokeCard";

const mutate = vi.hoisted(() => vi.fn());
const revokeState = vi.hoisted(() => ({ isPending: false }));
const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());

vi.mock("./hooks", () => ({
  useEmergencyRevokeAll: () => ({
    mutate,
    isPending: revokeState.isPending,
    error: null,
  }),
}));

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

describe("EmergencyRevokeCard", () => {
  beforeEach(() => {
    mutate.mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    revokeState.isPending = false;
  });
  afterEach(() => {
    mutate.mockReset();
  });

  it("renders the destructive trigger button", () => {
    renderWithProviders(<EmergencyRevokeCard />);
    const trigger = screen.getByTestId("emergency-revoke-trigger");
    expect(trigger).toBeInTheDocument();
    // The shared Button cva contract emits `bg-danger` on the
    // destructive (`variant="danger"`) variant. Asserting the
    // className keeps the test honest about the visual contract
    // rather than just checking the textContent.
    expect(trigger.className).toContain("bg-danger");
  });

  it("opens the confirmation dialog when the trigger is clicked", async () => {
    renderWithProviders(<EmergencyRevokeCard />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await userEvent.click(screen.getByTestId("emergency-revoke-trigger"));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(
      screen.getByTestId("emergency-revoke-confirm-input"),
    ).toBeInTheDocument();
  });

  it("keeps Confirm disabled until the exact phrase is typed", async () => {
    renderWithProviders(<EmergencyRevokeCard />);
    await userEvent.click(screen.getByTestId("emergency-revoke-trigger"));
    const confirm = screen.getByTestId("emergency-revoke-confirm");
    const input = screen.getByTestId("emergency-revoke-confirm-input");

    // empty
    expect(confirm).toBeDisabled();

    // wrong case must NOT enable the button
    await userEvent.type(input, "revoke all");
    expect(confirm).toBeDisabled();

    // partial match must NOT enable
    await userEvent.clear(input);
    await userEvent.type(input, "REVOKE");
    expect(confirm).toBeDisabled();

    // surrounding whitespace must NOT enable (no trim)
    await userEvent.clear(input);
    await userEvent.type(input, " REVOKE ALL");
    expect(confirm).toBeDisabled();

    await userEvent.clear(input);
    await userEvent.type(input, "REVOKE ALL ");
    expect(confirm).toBeDisabled();

    // exact match enables
    await userEvent.clear(input);
    await userEvent.type(input, "REVOKE ALL");
    expect(confirm).toBeEnabled();
  });

  it("fires the mutation with the typed reason on confirm", async () => {
    renderWithProviders(<EmergencyRevokeCard />);
    await userEvent.click(screen.getByTestId("emergency-revoke-trigger"));
    await userEvent.type(
      screen.getByTestId("emergency-revoke-confirm-input"),
      "REVOKE ALL",
    );
    await userEvent.type(
      screen.getByTestId("emergency-revoke-reason-input"),
      "leaked admin token",
    );
    await userEvent.click(screen.getByTestId("emergency-revoke-confirm"));
    expect(mutate).toHaveBeenCalledOnce();
    expect(mutate.mock.calls[0]?.[0]).toEqual({ reason: "leaked admin token" });
  });

  it("toasts success when the mutation resolves", async () => {
    mutate.mockImplementation(
      (
        _vars: { reason: string },
        opts: { onSuccess: (out: Record<string, unknown>) => void },
      ) => {
        opts.onSuccess({ ok: true });
      },
    );
    renderWithProviders(<EmergencyRevokeCard />);
    await userEvent.click(screen.getByTestId("emergency-revoke-trigger"));
    await userEvent.type(
      screen.getByTestId("emergency-revoke-confirm-input"),
      "REVOKE ALL",
    );
    await userEvent.click(screen.getByTestId("emergency-revoke-confirm"));
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
    expect(toastSuccess.mock.calls[0]?.[0]).toMatch(/Emergency revoke complete/);
  });

  it("toasts the error when the mutation rejects", async () => {
    mutate.mockImplementation(
      (
        _vars: { reason: string },
        opts: { onError: (err: Error) => void },
      ) => {
        opts.onError(new Error("rate limited"));
      },
    );
    renderWithProviders(<EmergencyRevokeCard />);
    await userEvent.click(screen.getByTestId("emergency-revoke-trigger"));
    await userEvent.type(
      screen.getByTestId("emergency-revoke-confirm-input"),
      "REVOKE ALL",
    );
    await userEvent.click(screen.getByTestId("emergency-revoke-confirm"));
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("rate limited"),
    );
  });
});
