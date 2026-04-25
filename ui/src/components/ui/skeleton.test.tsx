import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Skeleton } from "./skeleton";

describe("Skeleton", () => {
  it("renders a div with the animate-pulse token", () => {
    render(<Skeleton data-testid="sk" />);
    const el = screen.getByTestId("sk");
    expect(el.tagName).toBe("DIV");
    expect(el.className).toContain("animate-pulse");
  });

  it("uses the bg-bg-2 + rounded-md tokens", () => {
    render(<Skeleton data-testid="sk" />);
    const el = screen.getByTestId("sk");
    expect(el.className).toContain("bg-bg-2");
    expect(el.className).toContain("rounded-md");
  });

  it("forwards className without dropping defaults", () => {
    render(<Skeleton data-testid="sk" className="my-extra h-4" />);
    const el = screen.getByTestId("sk");
    expect(el.className).toContain("my-extra");
    expect(el.className).toContain("animate-pulse");
  });

  it("spreads arbitrary props to the underlying div", () => {
    render(<Skeleton data-testid="sk" aria-label="loading" />);
    const el = screen.getByTestId("sk");
    expect(el).toHaveAttribute("aria-label", "loading");
  });

  it("forwards ref to the underlying div", () => {
    const ref = { current: null as HTMLDivElement | null };
    render(<Skeleton ref={ref} />);
    expect(ref.current?.tagName).toBe("DIV");
  });

  it("renders children when provided", () => {
    render(
      <Skeleton>
        <span>inner</span>
      </Skeleton>,
    );
    expect(screen.getByText("inner")).toBeInTheDocument();
  });
});
