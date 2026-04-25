import { describe, it, vi } from "vitest";
import { renderWithProviders } from "@/test/render";
import { assertNoA11yViolations } from "@/test/a11y";

// Mirror CommandPalette.test.tsx mock surface. The palette uses
// Radix Dialog + cmdk; both ship axe-clean DOM, so we just need
// router/theme/hotkeys/toast stubs to mount the tree.
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => vi.fn(),
}));

vi.mock("./ThemeProvider", () => ({
  useTheme: () => ({
    setTheme: vi.fn(),
    resolvedTheme: "dark",
    theme: "dark",
  }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("react-hotkeys-hook", () => ({
  useHotkeys: () => undefined,
}));

import { CommandPalette } from "./CommandPalette";

describe("CommandPalette a11y", () => {
  it("renders open with no serious or critical axe violations", async () => {
    const { container } = renderWithProviders(
      <CommandPalette open onOpenChange={() => {}} />,
    );
    await assertNoA11yViolations(container);
  });
});
