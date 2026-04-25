import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

const locationState = { pathname: "/" };

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const actual = (await importOriginal()) as typeof import(
    "@tanstack/react-router"
  );
  return {
    ...actual,
    useLocation: () => locationState,
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
  };
});

import { Breadcrumb } from "./Breadcrumb";

describe("Breadcrumb", () => {
  it("renders just the root crumb at /", () => {
    locationState.pathname = "/";
    render(<Breadcrumb />);
    expect(screen.getByText("Media Stack")).toBeInTheDocument();
  });

  it("derives section + item from a known route", () => {
    locationState.pathname = "/content";
    render(<Breadcrumb />);
    expect(screen.getByText("Library")).toBeInTheDocument();
    expect(screen.getByText("Content")).toBeInTheDocument();
  });

  it("marks the leaf crumb with aria-current=page", () => {
    locationState.pathname = "/content";
    render(<Breadcrumb />);
    const current = screen.getByText("Content");
    expect(current).toHaveAttribute("aria-current", "page");
  });

  it("renders chevron separators between crumbs", () => {
    locationState.pathname = "/content";
    const { container } = render(<Breadcrumb />);
    // Chevrons are svg icons.
    expect(container.querySelectorAll("svg").length).toBeGreaterThanOrEqual(2);
  });

  it("non-leaf crumb is rendered as a link", () => {
    locationState.pathname = "/content";
    render(<Breadcrumb />);
    expect(screen.getByText("Library").tagName).toBe("A");
  });

  it("matches sub-paths via prefix", () => {
    locationState.pathname = "/logs/abc";
    render(<Breadcrumb />);
    expect(screen.getByText("Logs")).toBeInTheDocument();
    expect(screen.getByText("Library")).toBeInTheDocument();
  });

  it("falls back to root-only when path has no nav match", () => {
    locationState.pathname = "/totally-unknown";
    render(<Breadcrumb />);
    const current = screen.getByText("Media Stack");
    expect(current).toHaveAttribute("aria-current", "page");
  });

  it("uses the Workspace section label for SECONDARY_NAV items", () => {
    locationState.pathname = "/settings";
    render(<Breadcrumb />);
    expect(screen.getByText("Workspace")).toBeInTheDocument();
    expect(screen.getByText("Settings")).toBeInTheDocument();
  });
});
