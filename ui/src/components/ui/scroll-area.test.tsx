import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ScrollArea, ScrollBar } from "./scroll-area";

describe("ScrollArea", () => {
  it("renders the children inside the viewport", () => {
    render(
      <ScrollArea data-testid="root">
        <div>scroll body</div>
      </ScrollArea>,
    );
    expect(screen.getByText("scroll body")).toBeInTheDocument();
  });

  it("uses overflow-hidden + relative on the root", () => {
    render(
      <ScrollArea data-testid="root">
        <div>content</div>
      </ScrollArea>,
    );
    const root = screen.getByTestId("root");
    expect(root.className).toContain("relative");
    expect(root.className).toContain("overflow-hidden");
  });

  it("forwards className without dropping defaults", () => {
    render(
      <ScrollArea data-testid="root" className="extra-marker">
        <div>x</div>
      </ScrollArea>,
    );
    const root = screen.getByTestId("root");
    expect(root.className).toContain("extra-marker");
    expect(root.className).toContain("relative");
  });

  it("forwards ref on ScrollArea", () => {
    const ref = { current: null as HTMLDivElement | null };
    render(
      <ScrollArea ref={ref}>
        <div>x</div>
      </ScrollArea>,
    );
    expect(ref.current).not.toBeNull();
  });

  it("ScrollBar accepts orientation and defaults to vertical", () => {
    // Radix ScrollAreaScrollbar reads context off ScrollArea.Root, so
    // any ScrollBar render needs to live inside a ScrollArea. We pass
    // type="always" because the default "hover" type wraps the
    // scrollbar in a <Presence> that won't mount until a pointerenter
    // event lands; happy-dom never fires that, so the bar would
    // otherwise be invisible to assertions.
    render(
      <ScrollArea type="always">
        <div>x</div>
        <ScrollBar data-testid="sb" />
      </ScrollArea>,
    );
    const el = screen.getByTestId("sb");
    expect(el.getAttribute("data-orientation")).toBe("vertical");
    expect(el.className).toContain("w-2.5");
  });

  it("ScrollBar honors orientation=horizontal", () => {
    render(
      <ScrollArea type="always">
        <div>x</div>
        <ScrollBar data-testid="sb" orientation="horizontal" />
      </ScrollArea>,
    );
    const el = screen.getByTestId("sb");
    expect(el.getAttribute("data-orientation")).toBe("horizontal");
    expect(el.className).toContain("h-2.5");
  });
});
