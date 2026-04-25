// Command palette: cmdk-driven dialog opened via mod+k. Verify
// open/close, search, action firing, and the "g m" sequence shortcut.

import { expect, test } from "@playwright/test";
import { mockAll } from "./_mocks";

test.beforeEach(async ({ page }) => {
  await mockAll(page);
  await page.goto("/media-integrity");
  await expect(
    page.getByRole("heading", { level: 1, name: "Media Integrity" }),
  ).toBeVisible();
});

function modKey(browserName: string): "Meta" | "Control" {
  return browserName === "webkit" ? "Meta" : "Control";
}

test("opens with mod+k and closes with Escape", async ({ page, browserName }) => {
  const mod = modKey(browserName);
  await page.keyboard.press(`${mod}+KeyK`);
  const dialog = page.getByRole("dialog", { name: "Command palette" });
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
});

test("search narrows to Reconcile now and Enter fires the mutation", async ({
  page,
  browserName,
}) => {
  const mod = modKey(browserName);
  await page.keyboard.press(`${mod}+KeyK`);
  const dialog = page.getByRole("dialog", { name: "Command palette" });
  await expect(dialog).toBeVisible();

  await page.keyboard.type("rec");
  const item = dialog.getByRole("option", { name: /Reconcile now/i });
  await expect(item).toBeVisible();

  const postPromise = page.waitForRequest(
    (req) =>
      req.url().includes("/api/admin/reconcile") && req.method() === "POST",
  );
  await page.keyboard.press("Enter");
  const post = await postPromise;
  expect(post.url()).not.toContain("dry_run=1");
  await expect(dialog).toBeHidden();
});

test("g m navigates to /media-integrity from another tab", async ({ page }) => {
  // Hop to a placeholder route first so the navigation is observable.
  await page.goto("/content");
  await expect(page.getByRole("heading", { level: 1, name: "Content" })).toBeVisible();
  // The "g m" sequence: press g then m within the hotkey window.
  await page.keyboard.press("KeyG");
  await page.keyboard.press("KeyM");
  await expect(page).toHaveURL(/\/media-integrity$/);
});
