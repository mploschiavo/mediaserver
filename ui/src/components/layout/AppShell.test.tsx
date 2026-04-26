import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => vi.fn(),
  // The AppShell wires pull-to-refresh to `router.invalidate()`.
  useRouter: () => ({ invalidate: vi.fn(async () => {}) }),
  // BottomNav (rendered inside AppShell) reads the active path from
  // useRouterState. Stub returns a path that no nav item matches so
  // none gets the "active" class.
  useRouterState: () => "/__test_no_match__",
  // Tanstack Router's <Link> compiles down to an anchor; since this
  // mock replaces the whole module, supply a minimal type-correct
  // implementation that just renders an anchor.
  Link: ({ to, children, ...rest }: {
    to: string;
    children: React.ReactNode;
    [key: string]: unknown;
  }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("react-hotkeys-hook", () => ({
  // No-op hotkey wiring; we exercise the visual composition only.
  useHotkeys: () => undefined,
}));

vi.mock("./Sidebar", () => ({
  Sidebar: ({ onNavigate }: { onNavigate?: () => void }) => (
    <nav data-testid="sidebar-stub" onClick={onNavigate}>
      sidebar
    </nav>
  ),
}));

vi.mock("./TopBar", () => ({
  TopBar: (props: { onOpenSidebar: () => void; onOpenCommand: () => void }) => (
    <header data-testid="topbar-stub">
      <button onClick={props.onOpenSidebar}>open-sidebar</button>
      <button onClick={props.onOpenCommand}>open-command</button>
    </header>
  ),
}));

vi.mock("@/features/stack-lifecycle/UpgradeBanner", () => ({
  // The real banner pulls `useStackUpdate` (which would hit the
  // network); the shell tests don't care about it, so we stub a
  // visible div to assert presence.
  UpgradeBanner: () => <div data-testid="upgrade-banner-stub" />,
}));

// UpdateAvailableBanner reads `useStackUpdate` (Tanstack Query) the
// same way the upgrade banner does — stub to keep the shell tests
// from needing a QueryClient.
vi.mock("./UpdateAvailableBanner", () => ({
  UpdateAvailableBanner: () => <div data-testid="update-available-banner-stub" />,
}));

// The TriggeredBanner reads useGuardrails (network-bound). The
// AppShell tests render bare without a QueryClient so we stub it
// to a quiet div that is still asserted-against in mount-order tests.
vi.mock("@/features/guardrails", () => ({
  TriggeredBanner: () => <div data-testid="guardrails-banner-stub" />,
}));

// useHealth() drives the AlertEngine's poll callback. Stub a quiet
// snapshot so the engine has nothing to fire on; the AlertEngine
// itself is unit-tested separately.
vi.mock("@/api", async () => {
  const actual = await vi.importActual<typeof import("@/api")>("@/api");
  return {
    ...actual,
    useHealth: () => ({ data: { status: "ok" } }),
  };
});

const alertEngineStop = vi.hoisted(() => vi.fn());
const alertEngineStart = vi.hoisted(() =>
  vi.fn(() => ({ stop: alertEngineStop })),
);
vi.mock("@/features/alerts/AlertEngine", () => ({
  startAlertEngine: alertEngineStart,
}));

vi.mock("./CommandPalette", async () => {
  const { useState } = await import("react");
  return {
    CommandPalette: ({
      open,
      onOpenChange,
    }: {
      open: boolean;
      onOpenChange: (next: boolean) => void;
    }) => (
      <div
        data-testid="palette-stub"
        data-open={open ? "true" : "false"}
        onClick={() => onOpenChange(!open)}
      />
    ),
    useCommandPalette: () => {
      const [open, setOpen] = useState(false);
      return [open, setOpen] as const;
    },
  };
});

import { AppShell } from "./AppShell";

describe("AppShell", () => {
  it("renders Sidebar + TopBar + main content + CommandPalette", () => {
    render(
      <AppShell>
        <article>route content</article>
      </AppShell>,
    );
    // Both desktop & mobile drawer mount Sidebar; expect at least one.
    expect(screen.getAllByTestId("sidebar-stub").length).toBeGreaterThan(0);
    expect(screen.getByTestId("topbar-stub")).toBeInTheDocument();
    expect(screen.getByText("route content")).toBeInTheDocument();
    expect(screen.getByTestId("palette-stub")).toBeInTheDocument();
  });

  it("renders the UpgradeBanner before the TopBar in the content column", () => {
    render(
      <AppShell>
        <span>x</span>
      </AppShell>,
    );
    const banner = screen.getByTestId("upgrade-banner-stub");
    const topbar = screen.getByTestId("topbar-stub");
    expect(banner).toBeInTheDocument();
    // DOM order: banner must come before topbar.
    expect(
      banner.compareDocumentPosition(topbar) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("forwards the open-sidebar button click without erroring", async () => {
    render(
      <AppShell>
        <span>x</span>
      </AppShell>,
    );
    await userEvent.click(screen.getByText("open-sidebar"));
    expect(screen.getByTestId("topbar-stub")).toBeInTheDocument();
  });

  it("clicking 'open-command' flips the palette stub's data-open", async () => {
    render(
      <AppShell>
        <span>x</span>
      </AppShell>,
    );
    expect(screen.getByTestId("palette-stub")).toHaveAttribute(
      "data-open",
      "false",
    );
    await userEvent.click(screen.getByText("open-command"));
    expect(screen.getByTestId("palette-stub")).toHaveAttribute(
      "data-open",
      "true",
    );
  });

  it("renders the <main> region with its children", () => {
    const { container } = render(
      <AppShell>
        <span>main inner</span>
      </AppShell>,
    );
    expect(container.querySelector("main")).not.toBeNull();
    expect(screen.getByText("main inner")).toBeInTheDocument();
  });

  it("composes the desktop layout container with min-h-screen", () => {
    const { container } = render(
      <AppShell>
        <span>x</span>
      </AppShell>,
    );
    const root = container.firstChild as HTMLElement;
    expect(root.className).toContain("min-h-screen");
  });

  it("pins the desktop sidebar (sticky + h-screen at md+)", () => {
    // Ratchet: the desktop sidebar wrapper MUST carry md:sticky +
    // md:top-0 + md:h-screen so navigation stays in view as the
    // operator scrolls long pages. Reverted twice this session by
    // agent file-collisions — this test catches any future revert.
    const { container } = render(
      <AppShell>
        <span>x</span>
      </AppShell>,
    );
    // The sidebar wrapper is the first <aside>'s parent (the
    // ``hidden md:...`` wrapper around the Sidebar component).
    const wrapper = container.querySelector(".md\\:sticky");
    expect(wrapper, "sidebar wrapper missing md:sticky").not.toBeNull();
    const cls = wrapper?.className ?? "";
    expect(cls).toContain("md:sticky");
    expect(cls).toContain("md:top-0");
    expect(cls).toContain("md:h-screen");
  });

  it("renders a 'Skip to main content' link targeting #main-content", () => {
    render(
      <AppShell>
        <span>x</span>
      </AppShell>,
    );
    const skip = screen.getByRole("link", { name: /skip to main content/i });
    expect(skip).toHaveAttribute("href", "#main-content");
  });

  it("the <main> region exposes id=main-content + tabIndex=-1", () => {
    const { container } = render(
      <AppShell>
        <span>x</span>
      </AppShell>,
    );
    const main = container.querySelector("main");
    expect(main).not.toBeNull();
    expect(main?.id).toBe("main-content");
    expect(main?.getAttribute("tabindex")).toBe("-1");
  });

  it("starts the alert engine on mount and stops it on unmount", () => {
    alertEngineStart.mockClear();
    alertEngineStop.mockClear();
    const { unmount } = render(
      <AppShell>
        <span>x</span>
      </AppShell>,
    );
    expect(alertEngineStart).toHaveBeenCalledTimes(1);
    const firstCall = alertEngineStart.mock.calls[0] as
      | [{ pollHealth: () => unknown }]
      | undefined;
    expect(firstCall).toBeDefined();
    const opts = firstCall?.[0];
    expect(typeof opts?.pollHealth).toBe("function");
    expect(opts?.pollHealth()).toEqual({ status: "ok" });
    unmount();
    expect(alertEngineStop).toHaveBeenCalledTimes(1);
  });
});
