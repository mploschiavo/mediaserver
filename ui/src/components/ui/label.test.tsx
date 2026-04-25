import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { Label } from "./label";

describe("Label", () => {
  it("renders the children inside a label element", () => {
    render(<Label>Email</Label>);
    const el = screen.getByText("Email");
    expect(el.tagName).toBe("LABEL");
  });

  it("applies the default token classes", () => {
    render(<Label>Email</Label>);
    const el = screen.getByText("Email");
    expect(el.className).toContain("text-sm");
    expect(el.className).toContain("font-medium");
  });

  it("forwards className without dropping defaults", () => {
    render(<Label className="extra-x">Email</Label>);
    const el = screen.getByText("Email");
    expect(el.className).toContain("extra-x");
    expect(el.className).toContain("font-medium");
  });

  it("wires htmlFor to an input via the for attribute", () => {
    render(
      <>
        <Label htmlFor="email-field">Email</Label>
        <input id="email-field" />
      </>,
    );
    const label = screen.getByText("Email");
    expect(label).toHaveAttribute("for", "email-field");
  });

  it("clicking the label focuses the associated input", async () => {
    render(
      <>
        <Label htmlFor="email-field">Email</Label>
        <input id="email-field" data-testid="i" />
      </>,
    );
    await userEvent.click(screen.getByText("Email"));
    expect(screen.getByTestId("i")).toHaveFocus();
  });

  it("forwards ref to the underlying label", () => {
    const ref = { current: null as HTMLLabelElement | null };
    render(<Label ref={ref}>Email</Label>);
    expect(ref.current?.tagName).toBe("LABEL");
  });
});
