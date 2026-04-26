import { describe, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { assertNoA11yViolations } from "@/test/a11y";

// Same mocks as AppShell.test.tsx — we render the layout chrome,
// not the router/hotkey/sidebar internals. Each child has its own
// a11y test (or ships with axe-clean Radix primitives) so this file
// focuses on the shell composition: skip-link, <main>, drawer
// portal, bottom nav.
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => vi.fn(),
  useRouter: () => ({ invalidate: vi.fn(async () => {}) }),
  useRouterState: () => "/__test_no_match__",
  Link: ({
    to,
    children,
    ...rest
  }: {
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
  useHotkeys: () => undefined,
}));

vi.mock("./Sidebar", () => ({
  Sidebar: ({ onNavigate }: { onNavigate?: () => void }) => (
    <nav aria-label="Primary" data-testid="sidebar-stub" onClick={onNavigate}>
      <a href="/content">Content</a>
    </nav>
  ),
}));

vi.mock("./TopBar", () => ({
  TopBar: (props: { onOpenSidebar: () => void; onOpenCommand: () => void }) => (
    <header data-testid="topbar-stub">
      <button type="button" onClick={props.onOpenSidebar}>
        Open sidebar
      </button>
      <button type="button" onClick={props.onOpenCommand}>
        Open command palette
      </button>
    </header>
  ),
}));

vi.mock("@/features/stack-lifecycle/UpgradeBanner", () => ({
  // The real banner pulls `useStackUpdate` (a Tanstack Query useQuery
  // call) which would crash without a QueryClient in scope. The
  // a11y test is about the shell chrome, not the banner internals;
  // stub it out with a passive landmark-friendly placeholder.
  UpgradeBanner: () => null,
}));

// UpdateAvailableBanner shares the `useStackUpdate` query path; stub
// it for the same QueryClient-not-in-scope reason.
vi.mock("./UpdateAvailableBanner", () => ({
  UpdateAvailableBanner: () => null,
}));

// TriggeredBanner reads useGuardrails (Tanstack Query); same
// QueryClient-not-in-scope story as UpgradeBanner. Quiet stub.
vi.mock("@/features/guardrails", () => ({
  TriggeredBanner: () => null,
}));

// AppShell now wires the AlertEngine via `useHealth()` (wave 5);
// stub the hook so the a11y test doesn't need a QueryClient.
vi.mock("@/api", () => ({
  useHealth: () => ({ data: { status: "ok" }, isLoading: false, error: null }),
}));

vi.mock("@/features/alerts/AlertEngine", () => ({
  startAlertEngine: vi.fn(() => ({ stop: vi.fn() })),
}));

vi.mock("./CommandPalette", async () => {
  const { useState } = await import("react");
  return {
    CommandPalette: ({ open }: { open: boolean }) =>
      open ? <div role="dialog" aria-label="Command palette" /> : null,
    useCommandPalette: () => {
      const [open, setOpen] = useState(false);
      return [open, setOpen] as const;
    },
  };
});

import { AppShell } from "./AppShell";

describe("AppShell a11y", () => {
  it("renders with no serious or critical axe violations", async () => {
    const { container } = render(
      <AppShell>
        <article>
          <h1>Route content</h1>
          <p>Body copy used so the route region is non-empty.</p>
        </article>
      </AppShell>,
    );
    await assertNoA11yViolations(container);
  });
});
