import { describe, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { assertNoA11yViolations } from "@/test/a11y";

// Mirror UserMenu.test.tsx mock surface — we just need the router
// and toast stubs to mount the dropdown trigger; the menu itself is
// pure Radix DropdownMenu + cmdk, which axe is happy with so long as
// every menuitem has accessible text (it does).
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { UserMenu } from "./UserMenu";

describe("UserMenu a11y", () => {
  it("renders the open menu with no serious or critical axe violations", async () => {
    const { container } = render(
      <UserMenu name="Alice Liddell" email="alice@example.com" />,
    );
    // Open the menu: the Radix DropdownMenu Content only mounts
    // (and exposes its menuitems to the a11y tree) once the trigger
    // is activated.
    await userEvent.click(
      screen.getByRole("button", { name: /open account menu/i }),
    );
    // Wait for the menu to be in the DOM.
    await screen.findByText("My profile");
    await assertNoA11yViolations(container);
  });
});
