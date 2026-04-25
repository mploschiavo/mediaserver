import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const levelState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const applyMutate = vi.hoisted(() => vi.fn());
const applyState = vi.hoisted(() => ({ isPending: false }));
const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useLogLevel: () => levelState,
    useSetLogLevel: () => ({
      mutate: applyMutate,
      isPending: applyState.isPending,
    }),
  };
});

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

import { LogLevelCard } from "./LogLevelCard";

function reset() {
  levelState.data = undefined;
  levelState.isLoading = false;
  levelState.error = null;
  applyMutate.mockReset();
  applyState.isPending = false;
  toastSuccess.mockReset();
  toastError.mockReset();
}

describe("LogLevelCard", () => {
  beforeEach(reset);

  it("renders the current-level badge", () => {
    levelState.data = { level: "info" };
    renderWithProviders(<LogLevelCard />);
    expect(screen.getByTestId("log-level-current")).toHaveTextContent("info");
  });

  it("renders the loading skeleton", () => {
    levelState.isLoading = true;
    renderWithProviders(<LogLevelCard />);
    expect(screen.getByTestId("log-level-loading")).toBeInTheDocument();
  });

  it("renders the error banner", () => {
    levelState.error = new Error("nope");
    renderWithProviders(<LogLevelCard />);
    expect(screen.getByTestId("log-level-error")).toHaveTextContent("nope");
  });

  it("does not render the debug warning when level is info", () => {
    levelState.data = { level: "info" };
    renderWithProviders(<LogLevelCard />);
    expect(screen.queryByTestId("log-level-debug-warning")).toBeNull();
  });

  it("renders the debug warning when level is debug", () => {
    levelState.data = { level: "debug" };
    renderWithProviders(<LogLevelCard />);
    expect(
      screen.getByTestId("log-level-debug-warning"),
    ).toBeInTheDocument();
  });

  it("fires the mutation when Apply is clicked", async () => {
    levelState.data = { level: "info" };
    renderWithProviders(<LogLevelCard />);
    await userEvent.click(screen.getByTestId("log-level-apply"));
    expect(applyMutate).toHaveBeenCalledOnce();
    expect(applyMutate.mock.calls[0]?.[0]).toEqual({ level: "info" });
  });

  it("toasts on success", async () => {
    levelState.data = { level: "info" };
    applyMutate.mockImplementation(
      (_vars: unknown, opts: { onSuccess: () => void }) => opts.onSuccess(),
    );
    renderWithProviders(<LogLevelCard />);
    await userEvent.click(screen.getByTestId("log-level-apply"));
    await waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith("Log level set to info"),
    );
  });

  it("toasts on failure", async () => {
    levelState.data = { level: "info" };
    applyMutate.mockImplementation(
      (_vars: unknown, opts: { onError: (e: Error) => void }) =>
        opts.onError(new Error("denied")),
    );
    renderWithProviders(<LogLevelCard />);
    await userEvent.click(screen.getByTestId("log-level-apply"));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("denied"));
  });
});
