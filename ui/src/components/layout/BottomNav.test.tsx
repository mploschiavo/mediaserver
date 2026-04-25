import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { BottomNav, BOTTOM_NAV_ITEMS } from "./BottomNav";
import { renderWithRouter } from "@/test/router";

const PATHS = BOTTOM_NAV_ITEMS.map((item) => item.to);

describe("BottomNav", () => {
  it("renders all four primary items as links", async () => {
    renderWithRouter(<BottomNav />, { paths: PATHS });
    await waitFor(() => {
      expect(screen.getByRole("link", { name: /library/i })).toBeInTheDocument();
    });
    expect(screen.getByRole("link", { name: /logs/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /ops/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /health/i })).toBeInTheDocument();
  });

  it("marks the active route with aria-current=page", async () => {
    renderWithRouter(<BottomNav />, {
      initialPath: "/logs",
      paths: PATHS,
    });
    await waitFor(() => {
      const active = screen.getByRole("link", { name: /logs/i });
      expect(active).toHaveAttribute("aria-current", "page");
    });
    // Other items must NOT carry the marker.
    expect(screen.getByRole("link", { name: /library/i })).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("treats nested paths as active for the parent route", async () => {
    renderWithRouter(<BottomNav />, {
      initialPath: "/media-integrity/sub",
      paths: [...PATHS, "/media-integrity/sub"],
    });
    await waitFor(() => {
      const active = screen.getByRole("link", { name: /health/i });
      expect(active).toHaveAttribute("aria-current", "page");
    });
  });

  it("applies md:hidden so the nav vanishes on tablet+", async () => {
    renderWithRouter(<BottomNav />, { paths: PATHS });
    const nav = await screen.findByRole("navigation", { name: "Primary" });
    expect(nav.className).toContain("md:hidden");
    expect(nav.className).toContain("fixed");
    expect(nav.className).toContain("bottom-0");
  });

  it("each item meets the 44x44 touch-target minimum", async () => {
    renderWithRouter(<BottomNav />, { paths: PATHS });
    await waitFor(() => {
      expect(screen.getByRole("link", { name: /library/i })).toBeInTheDocument();
    });
    for (const item of BOTTOM_NAV_ITEMS) {
      const link = screen.getByRole("link", {
        name: new RegExp(item.label, "i"),
      });
      expect(link.className).toContain("min-h-[44px]");
      expect(link.className).toContain("min-w-[44px]");
    }
  });

  it("renders an icon and label per item", async () => {
    const { container } = renderWithRouter(<BottomNav />, { paths: PATHS });
    await waitFor(() => {
      expect(screen.getAllByRole("link")).toHaveLength(BOTTOM_NAV_ITEMS.length);
    });
    expect(container.querySelectorAll("svg").length).toBeGreaterThanOrEqual(
      BOTTOM_NAV_ITEMS.length,
    );
  });
});
