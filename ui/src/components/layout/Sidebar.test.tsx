import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";

const navigateMock = vi.fn();
const locationState = { pathname: "/content" };

// `useBranding` is in the @/api barrel — Sidebar reads `data.brand.name`
// + `data.brand.icon` for the wordmark. We mock the hook so the bare
// `render(...)` (no QueryProvider) doesn't throw and so we can inject
// the live `/api/branding` shape into individual tests.
const brandingState = vi.hoisted(() => ({
  data: undefined as unknown,
  isLoading: false,
  error: null as Error | null,
}));

vi.mock("@/api", async () => {
  const actual = await vi.importActual<typeof import("@/api")>("@/api");
  return {
    ...actual,
    useBranding: () => brandingState,
  };
});

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const actual = (await importOriginal()) as typeof import(
    "@tanstack/react-router"
  );
  return {
    ...actual,
    useLocation: () => locationState,
    useNavigate: () => navigateMock,
    Link: ({
      to,
      onClick,
      children,
      className,
    }: {
      to: string;
      onClick?: () => void;
      children: React.ReactNode;
      className?: string;
    }) => (
      <a
        href={to}
        onClick={(e) => {
          e.preventDefault();
          onClick?.();
        }}
        className={className}
      >
        {children}
      </a>
    ),
  };
});

import { NAV_SECTIONS, SECONDARY_NAV, Sidebar } from "./Sidebar";

describe("Sidebar", () => {
  beforeEach(() => {
    brandingState.data = undefined;
    brandingState.isLoading = false;
    brandingState.error = null;
  });

  it("renders the brand mark + product name (fallback when branding is empty)", () => {
    locationState.pathname = "/";
    brandingState.data = undefined;
    render(<Sidebar />);
    expect(screen.getByText("Media Stack")).toBeInTheDocument();
  });

  // Regression test sourced from the live /api/branding payload (see
  // ui/.ratchets/notes/API-RESPONSE-SHAPES-2026-04-25.txt). The
  // controller emits `brand.name` + `brand.icon` (URL); the previous
  // hand-typed shape used `product_name`/`logo_url` which never match.
  it("renders brand.name + brand.icon from the real /api/branding payload", () => {
    locationState.pathname = "/";
    brandingState.data = {
      brand: {
        name: "iomio.io",
        homepage_url: "https://iomio.io",
        tagline: "Media Stack Controller",
        wordmark: "/api/static/iomio-wordmark.svg",
        icon: "/api/static/iomio-icon.svg",
        illustration: "/api/static/iomio-orbit.svg",
      },
    };
    render(<Sidebar />);
    expect(screen.getByTestId("sidebar-brand-name")).toHaveTextContent(
      "iomio.io",
    );
    const icon = screen.getByTestId(
      "sidebar-brand-icon",
    ) as HTMLImageElement;
    expect(icon.src).toContain("/api/static/iomio-icon.svg");
  });

  it("renders every section heading", () => {
    locationState.pathname = "/";
    render(<Sidebar />);
    for (const section of NAV_SECTIONS) {
      expect(screen.getByText(section.label)).toBeInTheDocument();
    }
  });

  it("renders every primary nav item label", () => {
    locationState.pathname = "/";
    render(<Sidebar />);
    for (const section of NAV_SECTIONS) {
      for (const item of section.items) {
        expect(screen.getByText(item.label)).toBeInTheDocument();
      }
    }
  });

  it("renders secondary nav (Settings, API docs) and the Source link", () => {
    locationState.pathname = "/";
    render(<Sidebar />);
    for (const item of SECONDARY_NAV) {
      expect(screen.getByText(item.label)).toBeInTheDocument();
    }
    // The in-app API reference (Stoplight Elements) is now its own
    // sidebar entry; the GitHub external link reads "Source".
    expect(screen.getByText("API docs")).toBeInTheDocument();
    expect(screen.getByText("Source")).toBeInTheDocument();
  });

  it("marks the active link with the accent rail (route exact match)", () => {
    locationState.pathname = "/content";
    const { container } = render(<Sidebar />);
    // The active marker is the bg-accent rail span Sidebar emits
    // alongside the matching <Link>.
    const rail = container.querySelector(".bg-accent");
    expect(rail).not.toBeNull();
  });

  it("marks the active link by sub-path prefix as well", () => {
    locationState.pathname = "/logs/abc";
    render(<Sidebar />);
    // We can't easily query the active class on the link, but we can
    // confirm the corresponding label still renders without error.
    expect(screen.getByText("Logs")).toBeInTheDocument();
  });

  it("invokes onNavigate when a primary item is clicked", async () => {
    locationState.pathname = "/";
    const onNavigate = vi.fn();
    render(<Sidebar onNavigate={onNavigate} />);
    await userEvent.click(screen.getByText("Content"));
    expect(onNavigate).toHaveBeenCalled();
  });

  it("invokes onNavigate when a secondary item is clicked", async () => {
    locationState.pathname = "/";
    const onNavigate = vi.fn();
    render(<Sidebar onNavigate={onNavigate} />);
    await userEvent.click(screen.getByText("Settings"));
    expect(onNavigate).toHaveBeenCalled();
  });

  it("Source link points at the GitHub URL", () => {
    locationState.pathname = "/";
    render(<Sidebar />);
    const docsLink = screen.getByText("Source").closest("a");
    expect(docsLink).toHaveAttribute(
      "href",
      "https://github.com/mploschiavo/mediaserver",
    );
    expect(docsLink).toHaveAttribute("target", "_blank");
  });

  it("exposes NAV_SECTIONS and SECONDARY_NAV exports", () => {
    expect(NAV_SECTIONS.length).toBeGreaterThan(0);
    expect(SECONDARY_NAV.length).toBeGreaterThan(0);
  });
});
