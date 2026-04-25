import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { SponsorCard } from "./SponsorCard";

describe("SponsorCard", () => {
  it("renders the support-the-project headline", () => {
    render(<SponsorCard />);
    expect(screen.getByText("Support the project")).toBeInTheDocument();
  });

  it("renders a PayPal donate link with the controller's hosted_button_id", () => {
    render(<SponsorCard />);
    const link = screen.getByTestId("sponsor-paypal-link") as HTMLAnchorElement;
    expect(link).toBeInTheDocument();
    expect(link.getAttribute("href")).toBe(
      "https://www.paypal.com/donate?hosted_button_id=XKDG7XXVEQK3W",
    );
    // Industry pattern for outbound paid links: noopener noreferrer + new tab.
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel") ?? "").toMatch(/noopener/);
    expect(link.getAttribute("rel") ?? "").toMatch(/noreferrer/);
  });

  it("renders the PayPal CC button image with explicit alt text", () => {
    render(<SponsorCard />);
    const img = screen.getByAltText(/donate with paypal/i);
    expect(img).toBeInTheDocument();
    expect(img.getAttribute("src") ?? "").toContain("paypalobjects.com");
  });
});
