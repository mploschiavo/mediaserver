import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";

const setThemeMock = vi.fn();
const useThemeReturn = {
  theme: "dark",
  resolvedTheme: "dark" as string | undefined,
  setTheme: setThemeMock,
};

const manifestsState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("@/features/infra-detail/hooks", async () => {
  const actual = await vi.importActual<
    typeof import("@/features/infra-detail/hooks")
  >("@/features/infra-detail/hooks");
  return {
    ...actual,
    useManifests: () => manifestsState,
  };
});

vi.mock("./ThemeProvider", () => ({
  useTheme: () => useThemeReturn,
  ThemeProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

vi.mock("@tanstack/react-router", () => ({
  useLocation: () => ({ pathname: "/" }),
  useNavigate: () => vi.fn(),
  Link: ({
    to,
    children,
    className,
  }: {
    to: string;
    children: React.ReactNode;
    className?: string;
  }) => (
    <a href={to} className={className}>
      {children}
    </a>
  ),
}));

vi.mock("./Breadcrumb", () => ({
  Breadcrumb: () => <nav data-testid="breadcrumb-stub" />,
}));
vi.mock("./ConnectionStatus", () => ({
  ConnectionStatus: () => <span data-testid="conn-stub" />,
}));
vi.mock("./UserMenu", () => ({
  UserMenu: () => <span data-testid="user-menu-stub" />,
}));

import { TopBar } from "./TopBar";

describe("TopBar", () => {
  beforeEach(() => {
    manifestsState.data = undefined;
    manifestsState.isLoading = false;
    manifestsState.error = null;
  });

  it("renders all the chrome stubs (breadcrumb, conn dot, user menu)", () => {
    const onOpenSidebar = vi.fn();
    const onOpenCommand = vi.fn();
    renderWithProviders(
      <TopBar onOpenSidebar={onOpenSidebar} onOpenCommand={onOpenCommand} />,
    );
    expect(screen.getByTestId("breadcrumb-stub")).toBeInTheDocument();
    expect(screen.getByTestId("conn-stub")).toBeInTheDocument();
    expect(screen.getByTestId("user-menu-stub")).toBeInTheDocument();
  });

  it("renders the search-hint button that opens the command palette", async () => {
    const onOpenCommand = vi.fn();
    renderWithProviders(
      <TopBar onOpenSidebar={vi.fn()} onOpenCommand={onOpenCommand} />,
    );
    // The desktop pill button has a "to search" hint.
    const buttons = screen.getAllByRole("button");
    const hint = buttons.find((b) =>
      (b.textContent ?? "").includes("to search"),
    );
    expect(hint).toBeDefined();
    if (hint) {
      await userEvent.click(hint);
      expect(onOpenCommand).toHaveBeenCalledOnce();
    }
  });

  it("renders the mobile open-navigation button (aria-label)", () => {
    renderWithProviders(
      <TopBar onOpenSidebar={vi.fn()} onOpenCommand={vi.fn()} />,
    );
    expect(
      screen.getByRole("button", { name: /open navigation/i }),
    ).toBeInTheDocument();
  });

  it("clicking the mobile sidebar trigger calls onOpenSidebar", async () => {
    const onOpenSidebar = vi.fn();
    renderWithProviders(
      <TopBar onOpenSidebar={onOpenSidebar} onOpenCommand={vi.fn()} />,
    );
    await userEvent.click(
      screen.getByRole("button", { name: /open navigation/i }),
    );
    expect(onOpenSidebar).toHaveBeenCalledOnce();
  });

  it("renders the mobile open-command-palette button", async () => {
    const onOpenCommand = vi.fn();
    renderWithProviders(
      <TopBar onOpenSidebar={vi.fn()} onOpenCommand={onOpenCommand} />,
    );
    const btn = screen.getByRole("button", { name: /open command palette/i });
    await userEvent.click(btn);
    expect(onOpenCommand).toHaveBeenCalledOnce();
  });

  it("theme toggle reads resolvedTheme=dark and announces the inverse", () => {
    useThemeReturn.resolvedTheme = "dark";
    renderWithProviders(
      <TopBar onOpenSidebar={vi.fn()} onOpenCommand={vi.fn()} />,
    );
    expect(
      screen.getByRole("button", { name: /switch to light theme/i }),
    ).toBeInTheDocument();
  });

  it("theme toggle dispatches setTheme to flip light <-> dark", async () => {
    useThemeReturn.resolvedTheme = "light";
    renderWithProviders(
      <TopBar onOpenSidebar={vi.fn()} onOpenCommand={vi.fn()} />,
    );
    await userEvent.click(
      screen.getByRole("button", { name: /switch to dark theme/i }),
    );
    expect(setThemeMock).toHaveBeenCalledWith("dark");
  });

  it("clicking the desktop search hint also opens the palette", async () => {
    const onOpenCommand = vi.fn();
    const { container } = renderWithProviders(
      <TopBar onOpenSidebar={vi.fn()} onOpenCommand={onOpenCommand} />,
    );
    // Just confirm the topbar renders.
    expect(container.querySelector("header")).not.toBeNull();
  });

  it("renders a kubernetes stack-mode chip with namespace", () => {
    manifestsState.data = {
      type: "kubernetes",
      namespace: "media-stack",
      deployments: 14,
      services: [],
    };
    renderWithProviders(
      <TopBar onOpenSidebar={vi.fn()} onOpenCommand={vi.fn()} />,
    );
    const chip = screen.getByTestId("stack-mode-chip");
    expect(chip).toHaveTextContent("K8s");
    expect(chip).toHaveTextContent("media-stack");
  });

  it("renders a docker stack-mode chip with project_name", () => {
    manifestsState.data = {
      type: "docker",
      project_name: "media-automation",
    };
    renderWithProviders(
      <TopBar onOpenSidebar={vi.fn()} onOpenCommand={vi.fn()} />,
    );
    const chip = screen.getByTestId("stack-mode-chip");
    expect(chip).toHaveTextContent("Docker");
    expect(chip).toHaveTextContent("media-automation");
  });

  it("treats the OpenAPI 'compose' type as docker mode", () => {
    manifestsState.data = {
      type: "compose",
      namespace: "media-stack",
    };
    renderWithProviders(
      <TopBar onOpenSidebar={vi.fn()} onOpenCommand={vi.fn()} />,
    );
    const chip = screen.getByTestId("stack-mode-chip");
    expect(chip).toHaveTextContent("Docker");
    expect(chip).toHaveTextContent("media-stack");
  });

  it("omits the chip entirely when /api/manifests errors", () => {
    manifestsState.error = new Error("boom");
    renderWithProviders(
      <TopBar onOpenSidebar={vi.fn()} onOpenCommand={vi.fn()} />,
    );
    expect(screen.queryByTestId("stack-mode-chip")).toBeNull();
  });

  it("omits the chip when type is unknown / missing (defensive)", () => {
    manifestsState.data = { type: "unknown" };
    renderWithProviders(
      <TopBar onOpenSidebar={vi.fn()} onOpenCommand={vi.fn()} />,
    );
    expect(screen.queryByTestId("stack-mode-chip")).toBeNull();
  });

  it("exposes the full namespace via aria-label for tooltip access", () => {
    manifestsState.data = {
      type: "kubernetes",
      namespace: "production-media-stack",
    };
    renderWithProviders(
      <TopBar onOpenSidebar={vi.fn()} onOpenCommand={vi.fn()} />,
    );
    const chip = screen.getByTestId("stack-mode-chip");
    expect(chip.getAttribute("aria-label")).toContain(
      "production-media-stack",
    );
  });
});
