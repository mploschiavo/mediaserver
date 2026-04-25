import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";

const mutate = vi.hoisted(() => vi.fn());
const isPending = vi.hoisted(() => ({ value: false }));
const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());

vi.mock("./hooks", () => ({
  useUpdateRouting: () => ({
    mutate,
    get isPending() {
      return isPending.value;
    },
  }),
}));

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

import { RoutingEditor } from "./RoutingEditor";

describe("RoutingEditor", () => {
  beforeEach(() => {
    mutate.mockReset();
    isPending.value = false;
    toastSuccess.mockReset();
    toastError.mockReset();
  });

  const initial = {
    strategy: "subdomain" as const,
    base_domain: "example.test",
    external_hostname: "media.example.test",
  };

  it("renders the editor seeded from the initial strategy", () => {
    renderWithProviders(<RoutingEditor initial={initial} />);
    expect(screen.getByTestId("routing-editor")).toBeInTheDocument();
    const baseInput = screen.getByTestId(
      "routing-editor-base-domain",
    ) as HTMLInputElement;
    expect(baseInput.value).toBe("example.test");
  });

  it("disables submit until something has changed", () => {
    renderWithProviders(<RoutingEditor initial={initial} />);
    const submit = screen.getByTestId("routing-editor-submit");
    expect(submit).toBeDisabled();
  });

  it("rejects invalid hostnames", () => {
    renderWithProviders(<RoutingEditor initial={initial} />);
    const baseInput = screen.getByTestId(
      "routing-editor-base-domain",
    ) as HTMLInputElement;
    fireEvent.change(baseInput, { target: { value: "https://bad host" } });
    expect(screen.getByText(/Looks invalid/i)).toBeInTheDocument();
    expect(screen.getByTestId("routing-editor-submit")).toBeDisabled();
  });

  it("shows a diff preview when changes exist", () => {
    renderWithProviders(<RoutingEditor initial={initial} />);
    const baseInput = screen.getByTestId(
      "routing-editor-base-domain",
    ) as HTMLInputElement;
    fireEvent.change(baseInput, { target: { value: "home.lan" } });
    fireEvent.click(screen.getByTestId("routing-editor-preview-toggle"));
    expect(screen.getByTestId("routing-editor-preview")).toHaveTextContent(
      "home.lan",
    );
  });

  it("submits only the changed keys and toasts success", async () => {
    mutate.mockImplementation(
      (
        _vars: unknown,
        opts: {
          onSuccess?: (r: { changed: string[] }) => void;
        },
      ) => {
        opts.onSuccess?.({ changed: ["base_domain", "gateway_host"] });
      },
    );
    const onSaved = vi.fn();
    renderWithProviders(<RoutingEditor initial={initial} onSaved={onSaved} />);
    const baseInput = screen.getByTestId(
      "routing-editor-base-domain",
    ) as HTMLInputElement;
    fireEvent.change(baseInput, { target: { value: "home.lan" } });
    fireEvent.click(screen.getByTestId("routing-editor-submit"));
    expect(mutate).toHaveBeenCalledTimes(1);
    const payload = mutate.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(payload).toEqual({ base_domain: "home.lan" });
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
    expect(onSaved).toHaveBeenCalled();
  });

  it("toasts the error on failure", async () => {
    mutate.mockImplementation(
      (_vars: unknown, opts: { onError?: (e: Error) => void }) => {
        opts.onError?.(new Error("bad request"));
      },
    );
    renderWithProviders(<RoutingEditor initial={initial} />);
    const baseInput = screen.getByTestId(
      "routing-editor-base-domain",
    ) as HTMLInputElement;
    fireEvent.change(baseInput, { target: { value: "home.lan" } });
    fireEvent.click(screen.getByTestId("routing-editor-submit"));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("bad request"));
  });

  it("invokes onCancel when Cancel is clicked", () => {
    const onCancel = vi.fn();
    renderWithProviders(
      <RoutingEditor initial={initial} onCancel={onCancel} />,
    );
    fireEvent.click(screen.getByTestId("routing-editor-cancel"));
    expect(onCancel).toHaveBeenCalled();
  });
});
