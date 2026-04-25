// Theme toggle: dark default (system pref), TopBar button flips
// the data-theme attribute, the choice survives reload via
// localStorage, and prefers-color-scheme drives the system fallback.

import { expect, test } from "@playwright/test";
import { mockAll } from "./_mocks";

async function readTheme(page: import("@playwright/test").Page): Promise<string | null> {
  return page.evaluate(() => document.documentElement.dataset.theme ?? null);
}

test.beforeEach(async ({ page }) => {
  await mockAll(page);
});

test("starts dark when no localStorage value is set", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/media-integrity");
  await expect(
    page.getByRole("heading", { level: 1, name: "Media Integrity" }),
  ).toBeVisible();
  await expect.poll(() => readTheme(page)).toBe("dark");
});

test("TopBar toggle flips dark -> light", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/media-integrity");
  await expect.poll(() => readTheme(page)).toBe("dark");

  const toggle = page.getByRole("button", { name: /Switch to light theme/i });
  await toggle.click();
  await expect.poll(() => readTheme(page)).toBe("light");
});

test("theme persists across reload", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/media-integrity");
  await page.getByRole("button", { name: /Switch to light theme/i }).click();
  await expect.poll(() => readTheme(page)).toBe("light");

  await page.reload();
  await expect(
    page.getByRole("heading", { level: 1, name: "Media Integrity" }),
  ).toBeVisible();
  await expect.poll(() => readTheme(page)).toBe("light");
});

test("prefers-color-scheme dark drives default when storage is empty", async ({
  page,
  context,
}) => {
  await context.clearCookies();
  await page.addInitScript(() => {
    try {
      window.localStorage.removeItem("theme");
    } catch {
      // noop
    }
  });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/media-integrity");
  await expect.poll(() => readTheme(page)).toBe("dark");
});
