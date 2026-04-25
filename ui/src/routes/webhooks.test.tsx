import type { ComponentType } from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const webhooksState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

const addMutate = vi.hoisted(() => vi.fn());
const deleteMutate = vi.hoisted(() => vi.fn());
const testMutate = vi.hoisted(() => vi.fn());
const arrState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());
const toastInfo = vi.hoisted(() => vi.fn());

vi.mock("@/features/webhooks/hooks", () => ({
  useWebhooks: () => webhooksState,
  useAddWebhook: () => ({ mutate: addMutate, isPending: false }),
  useDeleteWebhook: () => ({ mutate: deleteMutate, isPending: false }),
  useTestWebhooks: () => ({ mutate: testMutate, isPending: false }),
  useArrWebhooks: () => arrState,
}));

vi.mock("sonner", () => ({
  toast: {
    success: toastSuccess,
    error: toastError,
    info: toastInfo,
  },
}));

import { Route as WebhooksRoute } from "./webhooks";

const WebhooksPage = WebhooksRoute.options.component as ComponentType;

describe("webhooks route", () => {
  beforeEach(() => {
    webhooksState.data = undefined;
    webhooksState.isLoading = false;
    webhooksState.error = null;
    arrState.data = { services: [] };
    arrState.isLoading = false;
    arrState.error = null;
    addMutate.mockReset();
    deleteMutate.mockReset();
    testMutate.mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    toastInfo.mockReset();
  });

  it("shows skeletons while loading", () => {
    webhooksState.isLoading = true;
    renderWithProviders(<WebhooksPage />);
    expect(screen.getByTestId("webhooks-loading")).toBeInTheDocument();
  });

  it("renders the empty state when there are no webhooks", () => {
    webhooksState.data = { webhooks: [] };
    renderWithProviders(<WebhooksPage />);
    expect(screen.getByText(/No webhooks yet/i)).toBeInTheDocument();
  });

  it("renders rows when webhooks are present", () => {
    webhooksState.data = {
      webhooks: [
        {
          id: "wh1",
          url: "https://example.test/hook",
          events: ["movie.imported"],
          last_fired_at: new Date().toISOString(),
        },
      ],
    };
    renderWithProviders(<WebhooksPage />);
    const row = screen.getByTestId("webhook-row-wh1");
    expect(row).toBeInTheDocument();
    expect(within(row).getByText("movie.imported")).toBeInTheDocument();
  });

  it("disables submit until URL and event are picked", async () => {
    webhooksState.data = { webhooks: [] };
    renderWithProviders(<WebhooksPage />);
    const submit = screen.getByTestId("webhook-add-submit");
    expect(submit).toBeDisabled();
    await userEvent.type(
      screen.getByTestId("webhook-url-input"),
      "https://e.test/h",
    );
    expect(submit).toBeDisabled();
  });

  it("posts the form via useAddWebhook on submit", async () => {
    webhooksState.data = { webhooks: [] };
    renderWithProviders(<WebhooksPage />);
    await userEvent.type(
      screen.getByTestId("webhook-url-input"),
      "https://e.test/h",
    );
    // Pick an event from the Select. Radix-select uses keyboard
    // semantics: open then select the option by role.
    await userEvent.click(screen.getByTestId("webhook-event-select"));
    await userEvent.click(
      await screen.findByRole("option", { name: "movie.imported" }),
    );
    await userEvent.click(screen.getByTestId("webhook-add-submit"));
    expect(addMutate).toHaveBeenCalledOnce();
    expect(addMutate.mock.calls[0]?.[0]).toEqual({
      url: "https://e.test/h",
      event_type: "movie.imported",
    });
  });

  it("calls deleteWebhook with the row id when the trash button is clicked", async () => {
    webhooksState.data = {
      webhooks: [
        {
          id: "wh-del",
          url: "https://e.test/h",
          events: ["movie.imported"],
        },
      ],
    };
    renderWithProviders(<WebhooksPage />);
    await userEvent.click(screen.getByTestId("webhook-delete-wh-del"));
    expect(deleteMutate).toHaveBeenCalledOnce();
    expect(deleteMutate.mock.calls[0]?.[0]).toEqual({ id: "wh-del" });
  });

  it("toasts a result per URL when test-all completes", async () => {
    testMutate.mockImplementation(
      (
        _v: undefined,
        opts: { onSuccess: (out: Record<string, unknown>) => void },
      ) => {
        opts.onSuccess({
          status: "tested",
          tested: 2,
          results: {
            "https://a/x": "ok (200)",
            "https://b/y": "timeout",
          },
        });
      },
    );
    renderWithProviders(<WebhooksPage />);
    await userEvent.click(screen.getByTestId("webhook-test-all"));
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
    expect(toastSuccess).toHaveBeenCalledWith(
      expect.stringContaining("ok (200)"),
    );
    expect(toastError).toHaveBeenCalledWith(
      expect.stringContaining("timeout"),
    );
  });

  it("toasts info when no webhooks are registered for test-all", async () => {
    testMutate.mockImplementation(
      (
        _v: undefined,
        opts: { onSuccess: (out: Record<string, unknown>) => void },
      ) => {
        opts.onSuccess({ status: "no_webhooks", tested: 0 });
      },
    );
    renderWithProviders(<WebhooksPage />);
    await userEvent.click(screen.getByTestId("webhook-test-all"));
    await waitFor(() => expect(toastInfo).toHaveBeenCalled());
  });

  it("renders the ArrWebhooksCard", () => {
    arrState.data = {
      services: [{ service: "sonarr", configured: true }],
    };
    renderWithProviders(<WebhooksPage />);
    expect(screen.getByTestId("arr-webhooks-card")).toBeInTheDocument();
  });

  it("shows error banner when webhook query fails", () => {
    webhooksState.error = new Error("explode");
    renderWithProviders(<WebhooksPage />);
    expect(screen.getByTestId("webhooks-error")).toHaveTextContent("explode");
  });
});
