import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const ghState = vi.hoisted(() => ({
  data: undefined as { hostnames?: string[] } | undefined,
  isLoading: false,
  error: null as Error | null,
  refetch: vi.fn(),
}));

const writeText = vi.hoisted(() => vi.fn().mockResolvedValue(undefined));
const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());

vi.mock("./hooks", () => ({
  useGatewayHostnames: () => ghState,
}));

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

import { GatewayHostnamesCard } from "./GatewayHostnamesCard";

describe("GatewayHostnamesCard", () => {
  beforeEach(() => {
    ghState.data = undefined;
    ghState.isLoading = false;
    ghState.error = null;
    writeText.mockClear();
    writeText.mockResolvedValue(undefined);
    toastSuccess.mockReset();
    toastError.mockReset();
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
  });

  it("renders skeleton while loading", () => {
    ghState.isLoading = true;
    renderWithProviders(<GatewayHostnamesCard />);
    expect(
      screen.getByTestId("gateway-hostnames-loading"),
    ).toBeInTheDocument();
  });

  it("renders an empty state when no hostnames are configured", () => {
    ghState.data = { hostnames: [] };
    renderWithProviders(<GatewayHostnamesCard />);
    expect(screen.getByText(/No hostnames/i)).toBeInTheDocument();
  });

  it("renders one row per hostname", () => {
    ghState.data = {
      hostnames: ["a.example.test", "b.example.test"],
    };
    renderWithProviders(<GatewayHostnamesCard />);
    expect(
      screen.getByTestId("gateway-hostname-a.example.test"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("gateway-hostname-b.example.test"),
    ).toBeInTheDocument();
  });

  it("renders an error banner when the query fails", () => {
    ghState.error = new Error("envoy down");
    renderWithProviders(<GatewayHostnamesCard />);
    expect(screen.getByTestId("gateway-hostnames-error")).toHaveTextContent(
      "envoy down",
    );
  });

  it("copies a hostname to the clipboard when the row button is clicked", async () => {
    ghState.data = { hostnames: ["a.example.test"] };
    renderWithProviders(<GatewayHostnamesCard />);
    fireEvent.click(
      screen.getByTestId("gateway-hostname-copy-a.example.test"),
    );
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith("a.example.test"),
    );
    await waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith("Copied a.example.test"),
    );
  });

  it("toasts an error when clipboard fails", async () => {
    writeText.mockRejectedValueOnce(new Error("denied"));
    ghState.data = { hostnames: ["a.example.test"] };
    renderWithProviders(<GatewayHostnamesCard />);
    fireEvent.click(
      screen.getByTestId("gateway-hostname-copy-a.example.test"),
    );
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("Clipboard unavailable"),
    );
  });
});
