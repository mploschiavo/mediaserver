import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { Button, buttonVariants } from "./button";

describe("Button", () => {
  it("renders its children", () => {
    render(<Button>Reconcile</Button>);
    expect(screen.getByRole("button", { name: "Reconcile" })).toBeInTheDocument();
  });

  it("applies the default variant + size when none specified", () => {
    render(<Button>X</Button>);
    const btn = screen.getByRole("button");
    // Default variant string surface (one stable token from the variant
    // class, not the whole class — we don't pin Tailwind output exactly).
    expect(btn.className).toContain("bg-bg-2");
    expect(btn.className).toContain("h-9");
  });

  it.each([
    ["primary", "bg-accent"],
    ["secondary", "bg-bg-1"],
    ["ghost", "bg-transparent"],
    ["danger", "bg-danger"],
    ["outline", "border-border-strong"],
  ] as const)("applies %s variant", (variant, marker) => {
    render(<Button variant={variant}>X</Button>);
    expect(screen.getByRole("button").className).toContain(marker);
  });

  it.each([
    ["sm", "h-8"],
    ["md", "h-9"],
    ["lg", "h-11"],
    ["icon", "size-9"],
  ] as const)("applies %s size", (size, marker) => {
    render(<Button size={size}>X</Button>);
    expect(screen.getByRole("button").className).toContain(marker);
  });

  it("default (md) size is mobile-first: 44px on touch, 36px on sm+", () => {
    render(<Button>X</Button>);
    const cls = screen.getByRole("button").className;
    expect(cls).toContain("h-11");
    expect(cls).toContain("sm:h-9");
  });

  it("icon size is mobile-first: 44px on touch, 36px on sm+", () => {
    render(<Button size="icon">X</Button>);
    const cls = screen.getByRole("button").className;
    expect(cls).toContain("size-11");
    expect(cls).toContain("sm:size-9");
  });

  it("forwards extra className without dropping variant classes", () => {
    render(<Button className="custom-x">X</Button>);
    const cls = screen.getByRole("button").className;
    expect(cls).toContain("custom-x");
    expect(cls).toContain("h-9");
  });

  it("renders loading state with spinner and disables the button", () => {
    render(<Button loading>Saving</Button>);
    const btn = screen.getByRole("button");
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("data-loading", "true");
    // The Loader2 svg is rendered as an aria-hidden child; assert
    // structurally rather than by accessible name.
    expect(btn.querySelector("svg")).not.toBeNull();
  });

  it("respects an explicit disabled prop even when not loading", () => {
    render(<Button disabled>Off</Button>);
    expect(screen.getByRole("button")).toBeDisabled();
  });

  it("does not render the spinner when not loading", () => {
    render(<Button>Idle</Button>);
    expect(screen.getByRole("button").querySelector("svg")).toBeNull();
    expect(screen.getByRole("button")).not.toHaveAttribute("data-loading");
  });

  it("fires onClick", async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Go</Button>);
    await userEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("does not fire onClick when disabled", async () => {
    const onClick = vi.fn();
    render(
      <Button onClick={onClick} disabled>
        No
      </Button>,
    );
    await userEvent.click(screen.getByRole("button"));
    expect(onClick).not.toHaveBeenCalled();
  });

  it("does not fire onClick when loading (loading implies disabled)", async () => {
    const onClick = vi.fn();
    render(
      <Button onClick={onClick} loading>
        Saving
      </Button>,
    );
    await userEvent.click(screen.getByRole("button"));
    expect(onClick).not.toHaveBeenCalled();
  });

  it("renders as a <a> when asChild is set and the child is an anchor", () => {
    // Button's render emits {loading ? <Loader/> : null}{children};
    // we render with loading=false (the default) so React.Children.toArray
    // collapses the spinner slot away and Slot can resolve to the
    // single anchor child.
    render(
      <Button asChild>
        <a href="/somewhere">Link</a>
      </Button>,
    );
    const link = screen.getByRole("link", { name: "Link" });
    expect(link.tagName).toBe("A");
    // Variant classes should still be applied via Slot composition.
    expect(link.className).toContain("h-9");
  });

  it("forwards ref to the underlying button element", () => {
    const ref: { current: HTMLButtonElement | null } = { current: null };
    render(<Button ref={ref}>R</Button>);
    expect(ref.current).not.toBeNull();
    expect(ref.current?.tagName).toBe("BUTTON");
  });

  it("exposes the buttonVariants helper for composition", () => {
    expect(typeof buttonVariants).toBe("function");
    const cls = buttonVariants({ variant: "primary", size: "lg" });
    expect(cls).toContain("bg-accent");
    expect(cls).toContain("h-11");
  });
});
