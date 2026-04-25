import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const certState = vi.hoisted(() => ({
  data: undefined as Record<string, unknown> | undefined,
  isLoading: false,
  error: null as Error | null,
  refetch: vi.fn(),
}));

const regenerateMutate = vi.hoisted(() => vi.fn());
const regeneratePending = vi.hoisted(() => ({ value: false }));

vi.mock("./hooks", () => ({
  useTlsCertificate: () => certState,
  useRegenerateTlsCertificate: () => ({
    mutate: regenerateMutate,
    get isPending() {
      return regeneratePending.value;
    },
  }),
}));

vi.mock("./TlsInstallDialog", () => ({
  TlsInstallDialog: ({ open }: { open: boolean }) =>
    open ? <div data-testid="tls-install-dialog-mock" /> : null,
}));

const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

import { TlsCertificateCard } from "./TlsCertificateCard";

describe("TlsCertificateCard", () => {
  beforeEach(() => {
    certState.data = undefined;
    certState.isLoading = false;
    certState.error = null;
    regenerateMutate.mockReset();
    regeneratePending.value = false;
    toastSuccess.mockReset();
    toastError.mockReset();
  });

  it("renders skeleton while loading", () => {
    certState.isLoading = true;
    renderWithProviders(<TlsCertificateCard />);
    expect(
      screen.getByTestId("tls-certificate-loading"),
    ).toBeInTheDocument();
  });

  it("renders cert details and the self-signed badge", () => {
    certState.data = {
      subject: "CN=media.example.test",
      issuer: "CN=Media Stack CA",
      san: ["media.example.test", "*.media.example.test"],
      valid_from: "2024-01-01T00:00:00Z",
      valid_to: "2025-01-01T00:00:00Z",
      fingerprint: "ab:cd:ef",
      self_signed: true,
    };
    renderWithProviders(<TlsCertificateCard />);
    expect(screen.getByTestId("tls-self-signed-badge")).toBeInTheDocument();
    expect(screen.getByText("CN=media.example.test")).toBeInTheDocument();
    expect(screen.getByText("CN=Media Stack CA")).toBeInTheDocument();
    expect(screen.getByText("media.example.test")).toBeInTheDocument();
    expect(screen.getByText("*.media.example.test")).toBeInTheDocument();
    expect(screen.getByText("ab:cd:ef")).toBeInTheDocument();
  });

  it("renders an error banner when the cert query fails", () => {
    certState.error = new Error("no cert configured");
    renderWithProviders(<TlsCertificateCard />);
    expect(screen.getByTestId("tls-certificate-error")).toHaveTextContent(
      "no cert configured",
    );
  });

  it("opens the regenerate confirmation dialog and fires the mutation on confirm", async () => {
    regenerateMutate.mockImplementation(
      (_v: unknown, opts: { onSuccess?: () => void }) => {
        opts.onSuccess?.();
      },
    );
    certState.data = { self_signed: true };
    renderWithProviders(<TlsCertificateCard />);
    fireEvent.click(screen.getByTestId("tls-certificate-regenerate"));
    expect(
      await screen.findByTestId("tls-regenerate-dialog"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("tls-regenerate-confirm"));
    expect(regenerateMutate).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith(
        "Self-signed certificate regenerated.",
      ),
    );
  });

  it("toasts on regenerate failure", async () => {
    regenerateMutate.mockImplementation(
      (_v: unknown, opts: { onError?: (e: Error) => void }) => {
        opts.onError?.(new Error("nope"));
      },
    );
    certState.data = {};
    renderWithProviders(<TlsCertificateCard />);
    fireEvent.click(screen.getByTestId("tls-certificate-regenerate"));
    fireEvent.click(await screen.findByTestId("tls-regenerate-confirm"));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("nope"));
  });

  it("opens the install-custom dialog when the button is clicked", () => {
    certState.data = {};
    renderWithProviders(<TlsCertificateCard />);
    fireEvent.click(screen.getByTestId("tls-certificate-install"));
    expect(
      screen.getByTestId("tls-install-dialog-mock"),
    ).toBeInTheDocument();
  });

  it("navigates to the download endpoint when Download is clicked", () => {
    certState.data = {};
    const assign = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...window.location, assign },
    });
    renderWithProviders(<TlsCertificateCard />);
    fireEvent.click(screen.getByTestId("tls-certificate-download"));
    expect(assign).toHaveBeenCalledWith("/api/tls/certificate/download");
  });
});
