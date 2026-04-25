import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Avatar, AvatarFallback, AvatarImage } from "./avatar";

describe("Avatar", () => {
  it("renders the root with default token classes", () => {
    render(
      <Avatar data-testid="root">
        <AvatarFallback>OP</AvatarFallback>
      </Avatar>,
    );
    const root = screen.getByTestId("root");
    expect(root.className).toContain("rounded-full");
    expect(root.className).toContain("size-9");
  });

  it("renders the fallback when no image is loaded", () => {
    render(
      <Avatar>
        <AvatarFallback>OP</AvatarFallback>
      </Avatar>,
    );
    expect(screen.getByText("OP")).toBeInTheDocument();
  });

  it("forwards className on Avatar root", () => {
    render(
      <Avatar data-testid="root" className="extra-marker">
        <AvatarFallback>OP</AvatarFallback>
      </Avatar>,
    );
    expect(screen.getByTestId("root").className).toContain("extra-marker");
  });

  it("forwards className on AvatarFallback", () => {
    render(
      <Avatar>
        <AvatarFallback className="fb-marker">OP</AvatarFallback>
      </Avatar>,
    );
    expect(screen.getByText("OP").className).toContain("fb-marker");
  });

  it("AvatarFallback uses default tokens", () => {
    render(
      <Avatar>
        <AvatarFallback>OP</AvatarFallback>
      </Avatar>,
    );
    const el = screen.getByText("OP");
    expect(el.className).toContain("bg-bg-2");
    expect(el.className).toContain("text-fg-muted");
  });

  it("forwards refs to root and fallback", () => {
    const root = { current: null as HTMLSpanElement | null };
    const fallback = { current: null as HTMLSpanElement | null };
    render(
      <Avatar ref={root}>
        <AvatarFallback ref={fallback}>OP</AvatarFallback>
      </Avatar>,
    );
    expect(root.current).not.toBeNull();
    expect(fallback.current).not.toBeNull();
  });

  it("AvatarImage exists as a forwardRef component", () => {
    // Avatar.Image only renders the <img> after load; in a happy-dom
    // test the load event won't fire, so we can't reliably assert
    // the rendered <img>. Instead, just confirm the export shape.
    expect(typeof AvatarImage).toBe("object");
  });
});
