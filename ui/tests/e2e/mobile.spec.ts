// Mobile-only checks. Pinned to the iPhone 15 project so the
// BottomNav, Vaul drawer, and PullToRefresh affordances all render.
// Other projects skip — desktop layouts hide the mobile chrome.

import { expect, test } from "@playwright/test";
import { mockAll } from "./_mocks";

test.beforeEach(async ({ page }, testInfo) => {
  test.skip(
    testInfo.project.name !== "chromium-iphone-15",
    "Mobile-only: requires the iPhone 15 viewport.",
  );
  await mockAll(page);
  await page.goto("/media-integrity");
  await expect(
    page.getByRole("heading", { level: 1, name: "Media Integrity" }),
  ).toBeVisible();
});

test("BottomNav is visible and Sidebar is hidden", async ({ page }) => {
  const bottomNav = page.getByTestId("bottom-nav");
  await expect(bottomNav).toBeVisible();
  // Desktop sidebar wrapper is hidden via the `hidden md:flex` class
  // at this breakpoint. The drawer's Sidebar lives behind a closed
  // overlay; assert no sidebar `aside` is currently visible.
  const visibleAsides = await page.locator("aside:visible").count();
  expect(visibleAsides).toBe(0);
});

test("hamburger opens the Vaul drawer with the full sidebar", async ({ page }) => {
  await page.getByRole("button", { name: "Open navigation" }).tap();
  // Vaul renders the drawer Title+Description as sr-only nodes.
  const navTitle = page.getByText("Navigation", { exact: true });
  await expect(navTitle).toBeAttached();
  // Now an aside (the Sidebar inside the drawer) should be in the tree.
  await expect(page.locator("aside").first()).toBeVisible();
});

test("BottomNav tap changes the URL", async ({ page }) => {
  const opsItem = page.getByRole("link", { name: /Ops/i }).last();
  await opsItem.tap();
  await expect(page).toHaveURL(/\/ops$/);
});

test("pull-to-refresh indicator appears on a downward swipe", async ({ page }) => {
  // The PullToRefresh wrapper exposes a `data-refreshing` attribute we
  // can assert on. Issue a synthetic touch swipe on the main scroll
  // area starting near the top.
  const indicator = page.getByTestId("pull-to-refresh-indicator");
  await expect(indicator).toBeAttached();
  const main = page.locator("main");
  const box = await main.boundingBox();
  expect(box).not.toBeNull();
  if (!box) return;
  const startX = box.x + box.width / 2;
  const startY = box.y + 20;
  await page.touchscreen.tap(startX, startY);
  // Use the CDP-backed swipe via dispatching a sequence of touch events.
  await page.evaluate(
    ({ x, y }) => {
      const target = document.querySelector('[data-testid="pull-to-refresh"]');
      if (!target) return;
      const make = (type: string, clientY: number) =>
        new TouchEvent(type, {
          bubbles: true,
          cancelable: true,
          touches:
            type === "touchend"
              ? []
              : [
                  new Touch({
                    identifier: 1,
                    target,
                    clientX: x,
                    clientY,
                  }),
                ],
        });
      target.dispatchEvent(make("touchstart", y));
      target.dispatchEvent(make("touchmove", y + 120));
      target.dispatchEvent(make("touchend", y + 120));
    },
    { x: startX, y: startY },
  );
  // Indicator height grows once a pull registers; assert non-zero
  // dataset state OR a non-collapsed bounding box.
  await expect
    .poll(async () => {
      const dataset = await indicator.getAttribute("data-refreshing");
      const bb = await indicator.boundingBox();
      return dataset === "true" || (bb !== null && bb.height > 0);
    })
    .toBe(true);
});
