import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const envState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));
const toastSuccess = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());

vi.mock("./hooks", async () => {
  const actual = await vi.importActual<typeof import("./hooks")>("./hooks");
  return {
    ...actual,
    useEffectiveEnv: () => envState,
  };
});

vi.mock("sonner", () => ({
  toast: { success: toastSuccess, error: toastError },
}));

import {
  EnvViewerCard,
  buildEnvFile,
  normalizeEnv,
} from "./EnvViewerCard";

function reset() {
  envState.data = undefined;
  envState.isLoading = false;
  envState.error = null;
  toastSuccess.mockReset();
  toastError.mockReset();
}

describe("EnvViewerCard", () => {
  beforeEach(reset);

  it("renders skeleton while loading", () => {
    envState.isLoading = true;
    renderWithProviders(<EnvViewerCard />);
    expect(screen.getByTestId("env-viewer-loading")).toBeInTheDocument();
  });

  it("groups rows by deployment / services / api-keys / other", () => {
    envState.data = {
      env: [
        { key: "GATEWAY_HOST", value: "media.local" },
        { key: "JELLYFIN_HOST", value: "jellyfin:8096" },
        { key: "JELLYFIN_API_KEY", value: "supersecret" },
        { key: "TZ", value: "UTC" },
      ],
    };
    renderWithProviders(<EnvViewerCard />);
    expect(screen.getByTestId("env-group-deployment")).toBeInTheDocument();
    expect(screen.getByTestId("env-group-services")).toBeInTheDocument();
    expect(screen.getByTestId("env-group-api-keys")).toBeInTheDocument();
    expect(screen.getByTestId("env-group-other")).toBeInTheDocument();
  });

  it("masks api-key values to a 'set'/'not set' badge", () => {
    envState.data = {
      env: [
        { key: "JELLYFIN_API_KEY", value: "supersecret" },
        { key: "RADARR_API_KEY", value: "" },
      ],
    };
    renderWithProviders(<EnvViewerCard />);
    const setRow = screen.getByTestId("env-row-JELLYFIN_API_KEY");
    expect(setRow).not.toHaveTextContent("supersecret");
    expect(screen.getByTestId("env-isset-JELLYFIN_API_KEY")).toHaveTextContent(
      "set",
    );
    expect(screen.getByTestId("env-isset-RADARR_API_KEY")).toHaveTextContent(
      "not set",
    );
  });

  it("filters rows by key substring", async () => {
    envState.data = {
      env: [
        { key: "GATEWAY_HOST", value: "media.local" },
        { key: "JELLYFIN_HOST", value: "jellyfin:8096" },
      ],
    };
    renderWithProviders(<EnvViewerCard />);
    await userEvent.type(screen.getByTestId("env-viewer-filter"), "JELL");
    expect(screen.getByTestId("env-row-JELLYFIN_HOST")).toBeInTheDocument();
    expect(screen.queryByTestId("env-row-GATEWAY_HOST")).toBeNull();
  });

  it("does not match sensitive values via the filter", async () => {
    envState.data = {
      env: [{ key: "JELLYFIN_API_KEY", value: "needle" }],
    };
    renderWithProviders(<EnvViewerCard />);
    await userEvent.type(screen.getByTestId("env-viewer-filter"), "needle");
    // Filter should hide the row — its key didn't match and the value
    // is sensitive (so the value-substring fallback is disabled).
    expect(screen.queryByTestId("env-row-JELLYFIN_API_KEY")).toBeNull();
  });

  it("supports the legacy `values` map fallback", () => {
    envState.data = { values: { LOG_LEVEL: "info" } };
    renderWithProviders(<EnvViewerCard />);
    expect(screen.getByTestId("env-row-LOG_LEVEL")).toHaveTextContent("info");
  });

  it("renders an export button that triggers a download (masked)", async () => {
    // happy-dom's URL inherits from NodeJS URL; some builds lack
    // `createObjectURL`/`revokeObjectURL`. Stub via property
    // assignment rather than `vi.spyOn` so we don't depend on the
    // method existing on the prototype.
    const createOrig = (URL as unknown as { createObjectURL?: unknown })
      .createObjectURL;
    const revokeOrig = (URL as unknown as { revokeObjectURL?: unknown })
      .revokeObjectURL;
    const createMock = vi.fn(() => "blob:abc");
    const revokeMock = vi.fn();
    (URL as unknown as { createObjectURL: unknown }).createObjectURL =
      createMock;
    (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL =
      revokeMock;

    const clickMock = vi.fn();
    const origClick = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = clickMock;

    try {
      envState.data = {
        env: [{ key: "GATEWAY_HOST", value: "media.local" }],
      };
      renderWithProviders(<EnvViewerCard />);
      await userEvent.click(screen.getByTestId("env-export-masked"));
      expect(createMock).toHaveBeenCalled();
      expect(clickMock).toHaveBeenCalled();
      expect(toastSuccess).toHaveBeenCalled();
    } finally {
      HTMLAnchorElement.prototype.click = origClick;
      (URL as unknown as { createObjectURL: unknown }).createObjectURL =
        createOrig;
      (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL =
        revokeOrig;
    }
  });

  it("disables export buttons when there are no rows", () => {
    envState.data = { env: [] };
    renderWithProviders(<EnvViewerCard />);
    expect(screen.getByTestId("env-export-masked")).toBeDisabled();
    expect(screen.getByTestId("env-export-unmasked")).toBeDisabled();
  });
});

describe("normalizeEnv", () => {
  it("categorizes known service host keys", () => {
    const rows = normalizeEnv({
      env: [
        { key: "GATEWAY_HOST", value: "x" },
        { key: "JELLYFIN_HOST", value: "y" },
        { key: "JELLYFIN_API_KEY", value: "secret" },
        { key: "ARBITRARY", value: "z" },
      ],
    });
    expect(rows.find((r) => r.key === "GATEWAY_HOST")?.category).toBe(
      "deployment",
    );
    expect(rows.find((r) => r.key === "JELLYFIN_HOST")?.category).toBe(
      "services",
    );
    expect(rows.find((r) => r.key === "JELLYFIN_API_KEY")?.category).toBe(
      "api-keys",
    );
    expect(rows.find((r) => r.key === "ARBITRARY")?.category).toBe("other");
  });
});

describe("buildEnvFile", () => {
  it("masks sensitive values when masked=true", () => {
    const body = buildEnvFile(
      [
        {
          key: "JELLYFIN_API_KEY",
          value: "supersecret",
          sensitive: true,
          category: "api-keys",
        },
        {
          key: "GATEWAY_HOST",
          value: "media.local",
          sensitive: false,
          category: "deployment",
        },
      ],
      true,
    );
    expect(body).toContain("JELLYFIN_API_KEY=");
    expect(body).not.toContain("supersecret");
    expect(body).toContain("GATEWAY_HOST=media.local");
  });

  it("includes secret values when masked=false", () => {
    const body = buildEnvFile(
      [
        {
          key: "JELLYFIN_API_KEY",
          value: "supersecret",
          sensitive: true,
          category: "api-keys",
        },
      ],
      false,
    );
    expect(body).toContain("JELLYFIN_API_KEY=supersecret");
  });

  it("quotes values containing whitespace", () => {
    const body = buildEnvFile(
      [
        {
          key: "MOTD",
          value: "hello world",
          sensitive: false,
          category: "other",
        },
      ],
      true,
    );
    expect(body).toContain('MOTD="hello world"');
  });
});
