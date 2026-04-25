// Critical path: status renders, adapter table renders, reconcile
// fires (real + dry-run), and the toast lands. All network
// interaction is mocked so the test is hermetic.

import { expect, test } from "@playwright/test";
import { mockAll } from "./_mocks";

test.beforeEach(async ({ page }) => {
  await mockAll(page);
  await page.goto("/media-integrity");
  await expect(
    page.getByRole("heading", { level: 1, name: "Media Integrity" }),
  ).toBeVisible();
});

test("StatusOverview renders three summary cards", async ({ page }) => {
  const overview = page.getByTestId("status-overview");
  await expect(overview).toBeVisible();
  // Three cards: bytes freed, last pass, configuration.
  await expect(overview.getByText("Bytes freed")).toBeVisible();
  await expect(overview.getByText("Last pass")).toBeVisible();
  await expect(overview.getByText("Configuration")).toBeVisible();
});

test("AdapterTable renders rows or the empty state", async ({ page }) => {
  const table = page.getByTestId("adapter-table");
  const empty = page.getByText("No adapters configured");
  // Whichever lands, exactly one of the two is in the DOM.
  await expect(table.or(empty)).toBeVisible();
  if (await table.isVisible()) {
    // The fixture configures radarr + sonarr + bazarr.
    await expect(table.getByText("Radarr")).toBeVisible();
    await expect(table.getByText("Sonarr")).toBeVisible();
  }
});

test("Reconcile now fires and surfaces a toast", async ({ page }) => {
  const button = page.getByTestId("reconcile-button");
  await expect(button).toHaveText(/Reconcile now/);
  const responsePromise = page.waitForResponse(
    (res) =>
      res.url().includes("/api/media-integrity/reconcile") &&
      res.request().method() === "POST",
  );
  await button.click();
  const response = await responsePromise;
  expect(response.url()).not.toContain("dry_run=1");
  // Toast text: "Reconcile complete — freed …".
  await expect(page.getByText(/Reconcile complete/i)).toBeVisible();
});

test("Dry-run toggle morphs the button label and threads dry_run=1", async ({
  page,
}) => {
  const toggle = page.getByTestId("reconcile-dry-run");
  const button = page.getByTestId("reconcile-button");

  await expect(button).toHaveText(/Reconcile now/);
  await toggle.check();
  await expect(button).toHaveText(/Dry-run reconcile/);

  const responsePromise = page.waitForResponse(
    (res) =>
      res.url().includes("/api/media-integrity/reconcile") &&
      res.request().method() === "POST",
  );
  await button.click();
  const response = await responsePromise;
  expect(response.url()).toContain("dry_run=1");
  await expect(page.getByText(/Dry-run preview/i)).toBeVisible();
});

test("Enforce config fires the secondary CTA", async ({ page }) => {
  const enforce = page.getByTestId("enforce-button");
  await expect(enforce).toBeVisible();
  const responsePromise = page.waitForResponse(
    (res) =>
      res.url().includes("/api/media-integrity/enforce-config") &&
      res.request().method() === "POST",
  );
  await enforce.click();
  await responsePromise;
  await expect(page.getByText(/compliant|Enforced/i)).toBeVisible();
});
