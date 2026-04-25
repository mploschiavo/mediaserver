import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Kbd } from "./kbd";

describe("Kbd", () => {
  it("renders a <kbd> element", () => {
    render(<Kbd>K</Kbd>);
    const el = screen.getByText("K");
    expect(el.tagName).toBe("KBD");
  });

  it("applies the default token classes", () => {
    render(<Kbd data-testid="k">K</Kbd>);
    const el = screen.getByTestId("k");
    expect(el.className).toContain("border-border");
    expect(el.className).toContain("bg-bg-2");
    expect(el.className).toContain("font-mono");
  });

  it("forwards className without dropping defaults", () => {
    render(
      <Kbd data-testid="k" className="extra-marker">
        K
      </Kbd>,
    );
    const el = screen.getByTestId("k");
    expect(el.className).toContain("extra-marker");
    expect(el.className).toContain("font-mono");
  });

  it("spreads attributes to the kbd element", () => {
    render(
      <Kbd data-testid="k" aria-label="keyboard">
        K
      </Kbd>,
    );
    expect(screen.getByTestId("k")).toHaveAttribute("aria-label", "keyboard");
  });

  it("forwards ref to the kbd element", () => {
    const ref = { current: null as HTMLElement | null };
    render(<Kbd ref={ref}>K</Kbd>);
    expect(ref.current?.tagName).toBe("KBD");
  });

  it("renders multiple children verbatim", () => {
    render(<Kbd>{"⌘K"}</Kbd>);
    expect(screen.getByText("⌘K")).toBeInTheDocument();
  });
});
