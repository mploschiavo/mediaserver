import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { SkeletonTable } from "./SkeletonTable";

describe("SkeletonTable", () => {
  it("renders the table chrome with aria-busy=true", () => {
    render(<SkeletonTable rows={2} columns={3} />);
    const root = screen.getByTestId("skeleton-table");
    expect(root).toBeInTheDocument();
    expect(root).toHaveAttribute("aria-busy", "true");
    expect(root.querySelector("table")).not.toBeNull();
  });

  it("renders the requested rows × columns of skeleton bars", () => {
    const { container } = render(<SkeletonTable rows={4} columns={5} />);
    // 5 header cells + 4*5 body cells = 25 skeleton bars total.
    expect(container.querySelectorAll(".animate-pulse").length).toBe(25);
    // Row count (1 header row + 4 body rows).
    expect(container.querySelectorAll("tr").length).toBe(5);
    // Header cell count.
    expect(container.querySelectorAll("th").length).toBe(5);
  });

  it("handles a 1×1 shape without errors", () => {
    const { container } = render(<SkeletonTable rows={1} columns={1} />);
    expect(container.querySelectorAll(".animate-pulse").length).toBe(2);
    expect(container.querySelectorAll("th").length).toBe(1);
    expect(container.querySelectorAll("td").length).toBe(1);
  });

  it("forwards className", () => {
    render(<SkeletonTable rows={1} columns={1} className="tbl-marker" />);
    expect(screen.getByTestId("skeleton-table").className).toContain(
      "tbl-marker",
    );
  });
});
