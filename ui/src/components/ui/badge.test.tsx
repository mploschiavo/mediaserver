import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Badge, badgeVariants } from "./badge";

describe("Badge", () => {
  it("renders its children inside a span", () => {
    render(<Badge>healed</Badge>);
    const el = screen.getByText("healed");
    expect(el.tagName).toBe("SPAN");
  });

  it("applies the default variant when none specified", () => {
    render(<Badge>x</Badge>);
    const el = screen.getByText("x");
    // default uses neutral fg-tinted background; pin a stable token
    expect(el.className).toContain("border-border");
  });

  it.each([
    ["success", "text-success"],
    ["warning", "text-warning"],
    ["danger", "text-danger"],
    ["info", "text-info"],
    ["outline", "border-border-strong"],
  ] as const)("applies %s variant", (variant, marker) => {
    render(<Badge variant={variant}>x</Badge>);
    expect(screen.getByText("x").className).toContain(marker);
  });

  it("forwards extra className", () => {
    render(<Badge className="foo-bar">x</Badge>);
    expect(screen.getByText("x").className).toContain("foo-bar");
  });

  it("forwards ref to the span element", () => {
    const ref: { current: HTMLSpanElement | null } = { current: null };
    render(<Badge ref={ref}>x</Badge>);
    expect(ref.current?.tagName).toBe("SPAN");
  });

  it("exposes badgeVariants for composition", () => {
    expect(badgeVariants({ variant: "success" })).toContain("text-success");
  });
});
