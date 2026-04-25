// Smoke coverage: visit every tab and verify the page renders within
// a reasonable budget, the heading matches, and the JS console stays
// clean. The spec runs on every project; the desktop projects use the
// sidebar nav and the mobile project relies on direct goto.

import { expect, test, type ConsoleMessage, type Page } from "@playwright/test";
import { mockAll } from "./_mocks";

interface TabSpec {
  path: string;
  heading: string;
}

const TABS: ReadonlyArray<TabSpec> = [
  { path: "/", heading: "Media Integrity" },
  { path: "/content", heading: "Content" },
  { path: "/logs", heading: "Logs" },
  { path: "/ops", heading: "Operations" },
  { path: "/routing", heading: "Routing" },
  { path: "/webhooks", heading: "Webhooks" },
  { path: "/users", heading: "Users" },
  { path: "/me", heading: "Me" },
  { path: "/media-integrity", heading: "Media Integrity" },
];

function attachConsoleSink(page: Page): { errors: string[] } {
  const errors: string[] = [];
  page.on("console", (msg: ConsoleMessage) => {
    if (msg.type() === "error") {
      const text = msg.text();
      // React's "act(...)" warnings and dev-only HMR pings are noisy
      // and not real product errors; filter them out.
      if (/Download the React DevTools/i.test(text)) return;
      if (/\[vite\]/i.test(text)) return;
      errors.push(text);
    }
  });
  page.on("pageerror", (err) => {
    errors.push(err.message);
  });
  return { errors };
}

test.beforeEach(async ({ page }) => {
  await mockAll(page);
});

for (const tab of TABS) {
  test(`smoke: ${tab.path} renders within budget with no console errors`, async ({
    page,
  }) => {
    const sink = attachConsoleSink(page);
    const start = Date.now();
    await page.goto(tab.path);
    // Title is always present on every route's PageHeader.
    await expect(page.getByRole("heading", { level: 1, name: tab.heading })).toBeVisible();
    const elapsed = Date.now() - start;
    expect(elapsed, `route ${tab.path} took ${elapsed}ms`).toBeLessThan(3_000);
    expect(sink.errors, `console errors on ${tab.path}`).toEqual([]);
  });
}
