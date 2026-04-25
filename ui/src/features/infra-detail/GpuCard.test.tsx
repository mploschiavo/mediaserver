import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const gpuState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const enableMutate = vi.hoisted(() => vi.fn());
const enableState = vi.hoisted(() => ({
  mutate: enableMutate,
  isPending: false,
}));
const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());

vi.mock("./hooks", () => ({
  useGpu: () => gpuState,
  useEnableGpu: () => enableState,
}));

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

import { GpuCard } from "./GpuCard";

describe("GpuCard", () => {
  beforeEach(() => {
    gpuState.data = undefined;
    gpuState.isLoading = false;
    gpuState.error = null;
    enableState.isPending = false;
    enableMutate.mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
  });

  it("renders skeletons while loading", () => {
    gpuState.isLoading = true;
    renderWithProviders(<GpuCard />);
    expect(screen.getByTestId("gpu-loading")).toBeInTheDocument();
  });

  it("renders the error message when the query fails", () => {
    gpuState.error = new Error("nope");
    renderWithProviders(<GpuCard />);
    expect(screen.getByTestId("gpu-error")).toHaveTextContent("nope");
  });

  it("renders the no-GPU badge when nothing is detected", () => {
    gpuState.data = { detected: false, gpus: [] };
    renderWithProviders(<GpuCard />);
    expect(screen.getByTestId("gpu-badge-none")).toBeInTheDocument();
  });

  it("derives Intel QSV from gpus[].type when the boolean shortcut is missing", () => {
    gpuState.data = {
      detected: true,
      gpus: [
        {
          type: "intel/va-api",
          name: "GPU passed to jellyfin (/dev/dri)",
          devices: ["/dev/dri/renderD128"],
          container: "jellyfin",
        },
      ],
    };
    renderWithProviders(<GpuCard />);
    expect(screen.getByTestId("gpu-badge-intel")).toBeInTheDocument();
    expect(screen.getByTestId("gpu-device-list")).toBeInTheDocument();
    expect(screen.getByTestId("gpu-device-0")).toHaveTextContent("intel/va-api");
  });

  it("renders the NVIDIA badge when nvidia=true", () => {
    gpuState.data = { detected: true, nvidia: true, gpus: [] };
    renderWithProviders(<GpuCard />);
    expect(screen.getByTestId("gpu-badge-nvidia")).toBeInTheDocument();
  });

  it("disables the enable button when jellyfin is already configured", () => {
    gpuState.data = {
      detected: true,
      jellyfin_configured: true,
      jellyfin_has_gpu: true,
      gpus: [{ type: "intel/va-api" }],
    };
    renderWithProviders(<GpuCard />);
    expect(screen.getByTestId("gpu-badge-on")).toBeInTheDocument();
    expect(screen.getByTestId("gpu-enable")).toBeDisabled();
  });

  it("calls the enable mutation and toasts on success", async () => {
    enableMutate.mockImplementation(
      (_v: void, opts: { onSuccess: (r: unknown) => void }) =>
        opts.onSuccess({ status: "ok", note: "Jellyfin restarted." }),
    );
    gpuState.data = {
      detected: true,
      can_auto_configure: true,
      gpus: [{ type: "intel/va-api" }],
    };
    renderWithProviders(<GpuCard />);
    await userEvent.click(screen.getByTestId("gpu-enable"));
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
  });

  it("toasts the error string on a failed enable response", async () => {
    enableMutate.mockImplementation(
      (_v: void, opts: { onSuccess: (r: unknown) => void }) =>
        opts.onSuccess({ status: "error", error: "no gpu detected" }),
    );
    gpuState.data = {
      detected: true,
      can_auto_configure: true,
      gpus: [{ type: "intel/va-api" }],
    };
    renderWithProviders(<GpuCard />);
    await userEvent.click(screen.getByTestId("gpu-enable"));
    await waitFor(() => expect(toastError).toHaveBeenCalled());
  });
});
