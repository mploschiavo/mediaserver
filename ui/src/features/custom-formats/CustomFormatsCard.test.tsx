import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const sonarrState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const radarrState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const lidarrState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const readarrState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

const importMutate = vi.hoisted(() => vi.fn());
const importState = vi.hoisted(() => ({ isPending: false }));

const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useCustomFormats: (service: string) => {
      if (service === "sonarr") return sonarrState;
      if (service === "radarr") return radarrState;
      if (service === "lidarr") return lidarrState;
      return readarrState;
    },
    useImportCustomFormats: () => ({
      mutate: importMutate,
      ...importState,
    }),
  };
});

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

import { CustomFormatsCard } from "./CustomFormatsCard";

describe("CustomFormatsCard", () => {
  beforeEach(() => {
    sonarrState.data = undefined;
    sonarrState.isLoading = false;
    sonarrState.error = null;
    radarrState.data = undefined;
    radarrState.isLoading = false;
    radarrState.error = null;
    lidarrState.data = undefined;
    lidarrState.isLoading = false;
    lidarrState.error = null;
    readarrState.data = undefined;
    readarrState.isLoading = false;
    readarrState.error = null;
    importMutate.mockReset();
    importState.isPending = false;
    toastSuccess.mockReset();
    toastError.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders skeletons in the active tab while loading", () => {
    sonarrState.isLoading = true;
    renderWithProviders(<CustomFormatsCard />);
    expect(
      screen.getByTestId("custom-formats-loading-sonarr"),
    ).toBeInTheDocument();
  });

  it("renders an empty state per service when no formats exist", () => {
    sonarrState.data = { formats: [] };
    renderWithProviders(<CustomFormatsCard />);
    expect(
      screen.getByText(/No custom formats for sonarr/i),
    ).toBeInTheDocument();
  });

  it("renders the populated list of formats", () => {
    sonarrState.data = {
      formats: [
        { id: 1, name: "x265 (HD)", trash_id: "abc123" },
        { id: 2, name: "Remux Tier" },
      ],
    };
    renderWithProviders(<CustomFormatsCard />);
    expect(screen.getByText("x265 (HD)")).toBeInTheDocument();
    expect(screen.getByText("Remux Tier")).toBeInTheDocument();
    expect(screen.getByText(/trash abc123/)).toBeInTheDocument();
  });

  it("renders an error banner when the query fails", () => {
    sonarrState.error = new Error("kaboom");
    renderWithProviders(<CustomFormatsCard />);
    expect(
      screen.getByTestId("custom-formats-error-sonarr"),
    ).toHaveTextContent("kaboom");
  });

  it("switches to the radarr tab when clicked", async () => {
    sonarrState.data = { formats: [] };
    radarrState.data = {
      formats: [{ id: 9, name: "HDR10+ Bonus" }],
    };
    renderWithProviders(<CustomFormatsCard />);
    await userEvent.click(screen.getByTestId("custom-formats-tab-radarr"));
    expect(await screen.findByText("HDR10+ Bonus")).toBeInTheDocument();
  });

  it("opens the import dialog and validates JSON before submitting", async () => {
    sonarrState.data = { formats: [] };
    renderWithProviders(<CustomFormatsCard />);
    await userEvent.click(
      screen.getByTestId("custom-formats-import-trigger-sonarr"),
    );
    await waitFor(() =>
      expect(
        screen.getByTestId("custom-formats-import-dialog-sonarr"),
      ).toBeInTheDocument(),
    );
    await userEvent.type(
      screen.getByTestId("custom-formats-import-textarea-sonarr"),
      "not json",
    );
    await userEvent.click(
      screen.getByTestId("custom-formats-import-submit-sonarr"),
    );
    expect(
      screen.getByTestId("custom-formats-import-error-sonarr"),
    ).toBeInTheDocument();
    expect(importMutate).not.toHaveBeenCalled();
  });

  it("submits valid JSON to the import mutation", async () => {
    sonarrState.data = { formats: [] };
    importMutate.mockImplementation(
      (_v: unknown, opts: { onSuccess: () => void }) => opts.onSuccess(),
    );
    renderWithProviders(<CustomFormatsCard />);
    await userEvent.click(
      screen.getByTestId("custom-formats-import-trigger-sonarr"),
    );
    const textarea = screen.getByTestId(
      "custom-formats-import-textarea-sonarr",
    ) as HTMLTextAreaElement;
    // userEvent.type interprets braces as keyboard codes; the textarea
    // is controlled, so set the value via fireEvent.change for a clean
    // raw-JSON paste.
    fireEvent.change(textarea, { target: { value: '{"trash_id": "abc"}' } });
    await userEvent.click(
      screen.getByTestId("custom-formats-import-submit-sonarr"),
    );
    await waitFor(() => expect(importMutate).toHaveBeenCalledTimes(1));
    expect(importMutate.mock.calls[0]?.[0]).toMatchObject({
      service: "sonarr",
      content: '{"trash_id": "abc"}',
    });
    expect(toastSuccess).toHaveBeenCalled();
  });
});
