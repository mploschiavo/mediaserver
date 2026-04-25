import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const installMutate = vi.hoisted(() => vi.fn());
const installPending = vi.hoisted(() => ({ value: false }));

vi.mock("./hooks", () => ({
  useInstallTlsCertificate: () => ({
    mutate: installMutate,
    get isPending() {
      return installPending.value;
    },
  }),
}));

const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

import { TlsInstallDialog } from "./TlsInstallDialog";

const VALID_CERT_PEM = `-----BEGIN CERTIFICATE-----
MIIBszCCAVigAwIBAgIUDummyCertForTestingPurposesOnly00wCgYIKoZIzj0E
AwIwITEfMB0GA1UEAwwWdGVzdC5leGFtcGxlLnRlc3QwHhcNMjQwMTAxMDAwMDAw
-----END CERTIFICATE-----
`;

const VALID_KEY_PEM = `-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgxxxxxx
-----END PRIVATE KEY-----
`;

function makeFile(name: string, content: string): File {
  return new File([content], name, { type: "application/x-pem-file" });
}

describe("TlsInstallDialog", () => {
  beforeEach(() => {
    installMutate.mockReset();
    installPending.value = false;
    toastSuccess.mockReset();
    toastError.mockReset();
  });

  it("does not render when closed", () => {
    renderWithProviders(
      <TlsInstallDialog open={false} onOpenChange={() => {}} />,
    );
    expect(screen.queryByTestId("tls-install-dialog")).toBeNull();
  });

  it("renders the dialog when open", () => {
    renderWithProviders(
      <TlsInstallDialog open onOpenChange={() => {}} />,
    );
    expect(screen.getByTestId("tls-install-dialog")).toBeInTheDocument();
    expect(screen.getByTestId("tls-install-submit")).toBeDisabled();
  });

  it("rejects a non-PEM cert file", async () => {
    renderWithProviders(<TlsInstallDialog open onOpenChange={() => {}} />);
    const certInput = screen.getByTestId(
      "tls-install-cert-input",
    ) as HTMLInputElement;
    fireEvent.change(certInput, {
      target: {
        files: [makeFile("bogus.pem", "not a real PEM file")],
      },
    });
    await waitFor(() =>
      expect(
        screen.getByTestId("tls-install-cert-error"),
      ).toBeInTheDocument(),
    );
    expect(screen.getByTestId("tls-install-submit")).toBeDisabled();
  });

  it("rejects a non-PEM key file", async () => {
    renderWithProviders(<TlsInstallDialog open onOpenChange={() => {}} />);
    const keyInput = screen.getByTestId(
      "tls-install-key-input",
    ) as HTMLInputElement;
    fireEvent.change(keyInput, {
      target: {
        files: [makeFile("bogus.key", "garbage")],
      },
    });
    await waitFor(() =>
      expect(
        screen.getByTestId("tls-install-key-error"),
      ).toBeInTheDocument(),
    );
  });

  it("submits PEM contents to the install mutation when both files validate", async () => {
    installMutate.mockImplementation(
      (_v: unknown, opts: { onSuccess?: () => void }) => {
        opts.onSuccess?.();
      },
    );
    const onOpenChange = vi.fn();
    renderWithProviders(
      <TlsInstallDialog open onOpenChange={onOpenChange} />,
    );
    fireEvent.change(screen.getByTestId("tls-install-cert-input"), {
      target: { files: [makeFile("server.pem", VALID_CERT_PEM)] },
    });
    fireEvent.change(screen.getByTestId("tls-install-key-input"), {
      target: { files: [makeFile("server.key", VALID_KEY_PEM)] },
    });
    await waitFor(() =>
      expect(screen.getByTestId("tls-install-submit")).not.toBeDisabled(),
    );
    fireEvent.click(screen.getByTestId("tls-install-submit"));
    await waitFor(() => expect(installMutate).toHaveBeenCalled());
    const payload = installMutate.mock.calls[0]?.[0] as {
      cert_pem: string;
      key_pem: string;
    };
    expect(payload.cert_pem).toContain("BEGIN CERTIFICATE");
    expect(payload.key_pem).toContain("BEGIN PRIVATE KEY");
    await waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith(
        "TLS certificate installed.",
      ),
    );
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("toasts on install failure", async () => {
    installMutate.mockImplementation(
      (_v: unknown, opts: { onError?: (e: Error) => void }) => {
        opts.onError?.(new Error("server rejected"));
      },
    );
    renderWithProviders(<TlsInstallDialog open onOpenChange={() => {}} />);
    fireEvent.change(screen.getByTestId("tls-install-cert-input"), {
      target: { files: [makeFile("server.pem", VALID_CERT_PEM)] },
    });
    fireEvent.change(screen.getByTestId("tls-install-key-input"), {
      target: { files: [makeFile("server.key", VALID_KEY_PEM)] },
    });
    await waitFor(() =>
      expect(screen.getByTestId("tls-install-submit")).not.toBeDisabled(),
    );
    fireEvent.click(screen.getByTestId("tls-install-submit"));
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("server rejected"),
    );
  });
});
