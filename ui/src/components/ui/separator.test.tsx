import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Separator } from "./separator";

describe("Separator", () => {
  it("renders with horizontal orientation by default", () => {
    render(<Separator data-testid="s" decorative={false} />);
    const el = screen.getByTestId("s");
    expect(el).toHaveAttribute("data-orientation", "horizontal");
    expect(el.className).toContain("h-px");
    expect(el.className).toContain("w-full");
  });

  it("supports vertical orientation", () => {
    render(<Separator data-testid="s" decorative={false} orientation="vertical" />);
    const el = screen.getByTestId("s");
    expect(el).toHaveAttribute("data-orientation", "vertical");
    expect(el.className).toContain("w-px");
    expect(el.className).toContain("h-full");
  });

  it("uses bg-border by default", () => {
    render(<Separator data-testid="s" />);
    expect(screen.getByTestId("s").className).toContain("bg-border");
  });

  it("forwards className without dropping defaults", () => {
    render(<Separator data-testid="s" className="extra-x" />);
    const el = screen.getByTestId("s");
    expect(el.className).toContain("extra-x");
    expect(el.className).toContain("bg-border");
  });

  it("is decorative by default (role none / no separator role)", () => {
    render(<Separator data-testid="s" />);
    const el = screen.getByTestId("s");
    // Decorative separators shouldn't expose role=separator.
    expect(el.getAttribute("role")).not.toBe("separator");
  });

  it("exposes role=separator when not decorative", () => {
    render(<Separator decorative={false} data-testid="s" />);
    expect(screen.getByTestId("s").getAttribute("role")).toBe("separator");
  });

  it("forwards ref to the underlying element", () => {
    const ref = { current: null as HTMLDivElement | null };
    render(<Separator ref={ref} data-testid="s" />);
    expect(ref.current).not.toBeNull();
  });
});
