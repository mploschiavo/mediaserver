import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { SkeletonCard } from "./SkeletonCard";

describe("SkeletonCard", () => {
  it("renders a card-shaped wrapper with aria-busy=true", () => {
    render(<SkeletonCard />);
    const card = screen.getByTestId("skeleton-card");
    expect(card).toBeInTheDocument();
    expect(card).toHaveAttribute("aria-busy", "true");
  });

  it("renders 4 skeleton bars (1 heading + 3 body lines)", () => {
    const { container } = render(<SkeletonCard />);
    // The Skeleton component renders a div with `animate-pulse`.
    const bars = container.querySelectorAll(".animate-pulse");
    expect(bars.length).toBe(4);
  });

  it("forwards className without dropping the card defaults", () => {
    render(<SkeletonCard className="loader-marker" />);
    const card = screen.getByTestId("skeleton-card");
    expect(card.className).toContain("loader-marker");
    // Card root keeps its border + bg tokens.
    expect(card.className).toContain("border");
  });
});
