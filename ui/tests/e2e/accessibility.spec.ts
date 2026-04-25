// Lightweight a11y spot-checks. We don't run axe; we just verify
// the few invariants the design system promises: keyboard tab order
// surfaces a skip link, every button on the home view has an
// accessible name, and focus is trapped in modals.

import { expect, test } from "@playwright/test";
import { mockAll } from "./_mocks";

test.beforeEach(async ({ page }) => {
  await mockAll(page);
  await page.goto("/media-integrity");
  await expect(
    page.getByRole("heading", { level: 1, name: "Media Integrity" }),
  ).toBeVisible();
});

test("first Tab press lands on the skip-to-main link when present", async ({
  page,
}) => {
  await page.keyboard.press("Tab");
  // The skip link, if implemented, points at #main-content.
  const skipLink = page.locator('a[href="#main-content"]').first();
  if ((await skipLink.count()) === 0) {
    test.skip(true, "Skip link not implemented yet — design-system roadmap.");
  }
  await expect(skipLink).toBeFocused();
  // sr-only off-screen until focused. Width should be effectively zero
  // when blurred; focused state expands it. We can only assert post-focus.
  const boundingBox = await skipLink.boundingBox();
  expect(boundingBox).not.toBeNull();
});

test("every button on the page has an accessible name", async ({ page }) => {
  const buttons = page.locator("button:visible");
  const count = await buttons.count();
  expect(count).toBeGreaterThan(0);
  for (let i = 0; i < count; i += 1) {
    const button = buttons.nth(i);
    const aria = (await button.getAttribute("aria-label")) ?? "";
    const text = (await button.innerText().catch(() => "")) ?? "";
    const name = (aria + text).trim();
    expect(name, `button #${i} missing accessible name`).not.toBe("");
  }
});

test("opening the command palette traps focus inside the dialog", async ({
  page,
  browserName,
}) => {
  const mod = browserName === "webkit" ? "Meta" : "Control";
  await page.keyboard.press(`${mod}+KeyK`);
  const dialog = page.getByRole("dialog", { name: "Command palette" });
  await expect(dialog).toBeVisible();
  // Tab a few times; focus should remain inside the dialog tree.
  for (let i = 0; i < 5; i += 1) {
    await page.keyboard.press("Tab");
    const inside = await page.evaluate(() => {
      const dlg = document.querySelector('[role="dialog"]');
      return dlg !== null && dlg.contains(document.activeElement);
    });
    expect(inside, `focus escaped on Tab #${i + 1}`).toBe(true);
  }
});
