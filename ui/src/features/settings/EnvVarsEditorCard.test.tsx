import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const envVarsState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());
const fetcherMock = vi.hoisted(() =>
  vi.fn(() => Promise.resolve({ ok: true })),
);

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useEnvVars: () => envVarsState,
  };
});

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

vi.mock("@/api", async () => {
  const actual = await vi.importActual<typeof import("@/api")>("@/api");
  return {
    ...actual,
    fetcher: fetcherMock,
  };
});

import { EnvVarsEditorCard } from "./EnvVarsEditorCard";

function reset() {
  envVarsState.data = undefined;
  envVarsState.isLoading = false;
  envVarsState.error = null;
  toastSuccess.mockReset();
  toastError.mockReset();
  fetcherMock.mockReset();
  fetcherMock.mockImplementation(() => Promise.resolve({ ok: true }));
}

describe("EnvVarsEditorCard", () => {
  beforeEach(reset);

  it("renders an empty state when there are no vars", () => {
    envVarsState.data = { vars: [] };
    renderWithProviders(<EnvVarsEditorCard />);
    expect(screen.getByTestId("envvars-empty")).toBeInTheDocument();
  });

  it("renders rows from the server payload", () => {
    envVarsState.data = {
      vars: [
        { key: "HOSTNAME", value: "alpha" },
        { key: "DB_PASSWORD", value: "secret" },
      ],
    };
    renderWithProviders(<EnvVarsEditorCard />);
    expect(screen.getByTestId("envvars-row-HOSTNAME")).toBeInTheDocument();
    expect(screen.getByTestId("envvars-row-DB_PASSWORD")).toBeInTheDocument();
  });

  it("masks sensitive values until the Reveal button is pressed", async () => {
    envVarsState.data = {
      vars: [{ key: "DB_PASSWORD", value: "supersecret" }],
    };
    renderWithProviders(<EnvVarsEditorCard />);
    const valueInput = screen.getByTestId(
      "envvars-value-DB_PASSWORD",
    ) as HTMLInputElement;
    expect(valueInput.value).toBe("••••");
    expect(valueInput).toHaveAttribute("type", "password");
    await userEvent.click(screen.getByTestId("envvars-reveal-DB_PASSWORD"));
    const revealedInput = screen.getByTestId(
      "envvars-value-DB_PASSWORD",
    ) as HTMLInputElement;
    expect(revealedInput.value).toBe("supersecret");
    expect(revealedInput).toHaveAttribute("type", "text");
  });

  it("does not render a Reveal button for non-sensitive keys", () => {
    envVarsState.data = { vars: [{ key: "HOSTNAME", value: "alpha" }] };
    renderWithProviders(<EnvVarsEditorCard />);
    expect(screen.queryByTestId("envvars-reveal-HOSTNAME")).toBeNull();
  });

  it("adds a new row when Add is clicked", async () => {
    envVarsState.data = { vars: [] };
    renderWithProviders(<EnvVarsEditorCard />);
    await userEvent.click(screen.getByTestId("envvars-add"));
    const list = screen.getByTestId("envvars-list");
    expect(list.querySelectorAll("li").length).toBe(1);
  });

  it("posts to /api/envvars on per-row save", async () => {
    envVarsState.data = { vars: [{ key: "HOSTNAME", value: "alpha" }] };
    renderWithProviders(<EnvVarsEditorCard />);
    await userEvent.click(screen.getByTestId("envvars-save-HOSTNAME"));
    await waitFor(() => expect(fetcherMock).toHaveBeenCalled());
    const firstCall = fetcherMock.mock.calls[0] as [unknown, { method?: string } | undefined] | undefined;
    const [path, init] = firstCall ?? [undefined, undefined];
    expect(String(path)).toMatch(/api\/envvars$/);
    expect(init?.method).toBe("POST");
  });

  it("surfaces a Key required error for blank rows", async () => {
    envVarsState.data = { vars: [] };
    const { container } = renderWithProviders(<EnvVarsEditorCard />);
    await userEvent.click(screen.getByTestId("envvars-add"));
    const newRowSave = container.querySelector(
      '[data-testid^="envvars-save-new-"]',
    ) as HTMLButtonElement | null;
    expect(newRowSave).not.toBeNull();
    await userEvent.click(newRowSave!);
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("Key is required"),
    );
    expect(fetcherMock).not.toHaveBeenCalled();
  });
});
