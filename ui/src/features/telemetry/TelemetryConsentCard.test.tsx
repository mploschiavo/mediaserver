import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const prefsState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const saveMutate = vi.hoisted(() => vi.fn());
const saveState = vi.hoisted(() => ({ isPending: false }));
const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useTelemetry: () => prefsState,
    useSaveTelemetry: () => ({
      mutate: saveMutate,
      isPending: saveState.isPending,
    }),
  };
});

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError, warning: vi.fn() },
}));

import { TelemetryConsentCard } from "./TelemetryConsentCard";

function reset() {
  prefsState.data = undefined;
  prefsState.isLoading = false;
  prefsState.error = null;
  saveMutate.mockReset();
  saveState.isPending = false;
  toastSuccess.mockReset();
  toastError.mockReset();
}

describe("TelemetryConsentCard", () => {
  beforeEach(reset);

  it("shows the loading skeleton while prefs are loading", () => {
    prefsState.isLoading = true;
    renderWithProviders(<TelemetryConsentCard />);
    expect(screen.getByTestId("telemetry-loading")).toBeInTheDocument();
  });

  it("shows the error banner on failure", () => {
    prefsState.error = new Error("nope");
    renderWithProviders(<TelemetryConsentCard />);
    expect(screen.getByTestId("telemetry-error")).toHaveTextContent("nope");
  });

  it("shows 'none' when no consent is recorded", () => {
    prefsState.data = {};
    renderWithProviders(<TelemetryConsentCard />);
    expect(screen.getByTestId("telemetry-consent-badge")).toHaveTextContent(
      "none",
    );
    expect(
      screen.getByTestId("telemetry-categories-empty"),
    ).toBeInTheDocument();
  });

  it("renders the consent badge + opted-in categories from server data", () => {
    prefsState.data = {
      consent: "standard",
      categories: ["health_probes", "version"],
    };
    renderWithProviders(<TelemetryConsentCard />);
    expect(screen.getByTestId("telemetry-consent-badge")).toHaveTextContent(
      "standard",
    );
    const list = screen.getByTestId("telemetry-categories-list");
    expect(within(list).getByText("health_probes")).toBeInTheDocument();
    expect(within(list).getByText("version")).toBeInTheDocument();
  });

  it("includes the privacy banner copy", () => {
    prefsState.data = {};
    renderWithProviders(<TelemetryConsentCard />);
    expect(screen.getByTestId("telemetry-privacy-banner")).toBeInTheDocument();
    expect(
      screen.getByText(/never collect personal data/i),
    ).toBeInTheDocument();
  });

  it("submits the dialog selection via useSaveTelemetry", async () => {
    prefsState.data = {
      consent: "minimal",
      categories: ["crash"],
    };
    const user = userEvent.setup();
    renderWithProviders(<TelemetryConsentCard />);
    await user.click(screen.getByTestId("telemetry-update-trigger"));
    const dialog = await screen.findByTestId("telemetry-update-dialog");

    // Toggle a new category on
    await user.click(within(dialog).getByTestId("telemetry-cat-version"));
    await user.click(within(dialog).getByTestId("telemetry-submit"));

    await waitFor(() => expect(saveMutate).toHaveBeenCalledOnce());
    const call = saveMutate.mock.calls[0]![0] as {
      consent: string;
      categories: string[];
    };
    expect(call.consent).toBe("minimal");
    expect(call.categories).toContain("crash");
    expect(call.categories).toContain("version");
  });

  it("toasts on save success", async () => {
    prefsState.data = { consent: "none", categories: [] };
    saveMutate.mockImplementation(
      (_vars: unknown, opts: { onSuccess: () => void }) => opts.onSuccess(),
    );
    const user = userEvent.setup();
    renderWithProviders(<TelemetryConsentCard />);
    await user.click(screen.getByTestId("telemetry-update-trigger"));
    const dialog = await screen.findByTestId("telemetry-update-dialog");
    await user.click(within(dialog).getByTestId("telemetry-submit"));
    await waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith("Telemetry preferences saved"),
    );
  });

  it("toasts on save failure", async () => {
    prefsState.data = { consent: "none", categories: [] };
    saveMutate.mockImplementation(
      (_vars: unknown, opts: { onError: (e: Error) => void }) =>
        opts.onError(new Error("oops")),
    );
    const user = userEvent.setup();
    renderWithProviders(<TelemetryConsentCard />);
    await user.click(screen.getByTestId("telemetry-update-trigger"));
    const dialog = await screen.findByTestId("telemetry-update-dialog");
    await user.click(within(dialog).getByTestId("telemetry-submit"));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("oops"));
  });
});
