import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { Input } from "./input";

describe("Input", () => {
  it("renders an <input> with type=text by default", () => {
    render(<Input data-testid="i" />);
    const el = screen.getByTestId("i") as HTMLInputElement;
    expect(el.tagName).toBe("INPUT");
    expect(el.type).toBe("text");
  });

  it("accepts a non-default type prop", () => {
    render(<Input data-testid="i" type="email" />);
    expect((screen.getByTestId("i") as HTMLInputElement).type).toBe("email");
  });

  it("applies token classes", () => {
    render(<Input data-testid="i" />);
    const el = screen.getByTestId("i");
    expect(el.className).toContain("h-9");
    expect(el.className).toContain("border-input");
  });

  it("uses mobile-first font-size: text-base on touch, text-sm on sm+", () => {
    // 16px on mobile dodges iOS Safari's auto-zoom on focus; we
    // shrink to 14px from sm: up where pointer is mouse-class.
    render(<Input data-testid="i" />);
    const cls = screen.getByTestId("i").className;
    expect(cls).toContain("text-base");
    expect(cls).toContain("sm:text-sm");
  });

  it("forwards className without dropping defaults", () => {
    render(<Input data-testid="i" className="extra-x" />);
    const el = screen.getByTestId("i");
    expect(el.className).toContain("extra-x");
    expect(el.className).toContain("h-9");
  });

  it("forwards ref to the underlying input", () => {
    const ref = { current: null as HTMLInputElement | null };
    render(<Input ref={ref} />);
    expect(ref.current?.tagName).toBe("INPUT");
  });

  it("fires onChange when the user types", async () => {
    const onChange = vi.fn();
    render(<Input onChange={onChange} placeholder="x" />);
    await userEvent.type(screen.getByPlaceholderText("x"), "abc");
    expect(onChange).toHaveBeenCalled();
  });

  it("respects the disabled prop", () => {
    render(<Input data-testid="i" disabled />);
    expect(screen.getByTestId("i")).toBeDisabled();
  });

  it("does not fire onChange while disabled", async () => {
    const onChange = vi.fn();
    render(<Input data-testid="i" disabled onChange={onChange} />);
    await userEvent.type(screen.getByTestId("i"), "x");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("supports a controlled value", () => {
    render(<Input data-testid="i" value="hello" readOnly />);
    expect((screen.getByTestId("i") as HTMLInputElement).value).toBe("hello");
  });

  it("forwards arbitrary attributes (placeholder, name)", () => {
    render(<Input data-testid="i" placeholder="email" name="mail" />);
    const el = screen.getByTestId("i") as HTMLInputElement;
    expect(el.placeholder).toBe("email");
    expect(el.name).toBe("mail");
  });
});
