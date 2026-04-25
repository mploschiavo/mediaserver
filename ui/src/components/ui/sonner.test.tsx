import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { Toaster } from "./sonner";

describe("Toaster (sonner wrapper)", () => {
  it("renders without crashing", () => {
    const { container } = render(<Toaster />);
    expect(container).toBeTruthy();
  });

  it("forwards arbitrary ToasterProps without erroring", () => {
    const { container } = render(<Toaster position="top-right" />);
    expect(container).toBeTruthy();
  });

  it("Toaster export is a function component", () => {
    expect(typeof Toaster).toBe("function");
  });
});
