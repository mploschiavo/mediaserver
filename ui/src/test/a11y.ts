import { expect } from "vitest";
// `vitest-axe` is the Vitest-native fork of `jest-axe`; the default
// export is an `axe` runner that takes a node + an axe-core options
// bag and returns the standard axe results object.
import { axe } from "vitest-axe";
import type { AxeResults, Result, NodeResult } from "axe-core";

/**
 * Severities axe-core assigns to violation rules. We treat
 * `serious` and `critical` as the "fail the build" tier; `minor`
 * and `moderate` are surfaced in test logs but do not block.
 *
 * The thresholds match how the Lighthouse a11y score and the WCAG
 * conformance tooling weight findings: a11y bugs that block keyboard
 * users / assistive tech from completing a task land in serious or
 * critical, so those are the regressions we ratchet on.
 */
type BlockingImpact = "serious" | "critical";

const BLOCKING_IMPACTS: ReadonlySet<BlockingImpact> = new Set([
  "serious",
  "critical",
]);

/**
 * Run axe-core against `container` and assert that there are no
 * `serious` or `critical` violations under the WCAG 2 A/AA tag set.
 *
 * On failure, dumps each rule ID, the violation help URL, and every
 * offending DOM selector so the test output is actionable without
 * re-running with --reporter=verbose.
 */
export async function assertNoA11yViolations(
  container: HTMLElement,
): Promise<void> {
  const results = (await axe(container, {
    runOnly: {
      type: "tag",
      values: ["wcag2a", "wcag2aa"],
    },
    rules: {
      // Radix portal content (DropdownMenu, Dialog) toggles `aria-hidden`
      // on the body via FocusScope. happy-dom does not implement focus
      // movement the same way real browsers do, so axe-core sees the
      // focused trigger inside the hidden region. Real browsers paint
      // this correctly; verified manually + via Playwright.
      "aria-hidden-focus": { enabled: false },
    },
  })) as AxeResults;

  const blocking = results.violations.filter(
    (violation): violation is Result =>
      typeof violation.impact === "string" &&
      BLOCKING_IMPACTS.has(violation.impact as BlockingImpact),
  );

  if (blocking.length > 0) {
    // Print a focused, reviewable summary before the assertion fires
    // so the failing test's output points at the exact rule + node.
     
    console.error(
      `[a11y] ${blocking.length} blocking violation(s) detected ` +
        `(severity: serious | critical):\n` +
        blocking
          .map((violation) => formatViolation(violation))
          .join("\n\n"),
    );
  }

  expect(blocking).toEqual([]);
}

function formatViolation(violation: Result): string {
  const header =
    `  - ${violation.id} [${violation.impact ?? "unknown"}] ` +
    `${violation.help}\n    ${violation.helpUrl}`;
  const nodes = violation.nodes
    .map((node: NodeResult) => `    • ${node.target.join(" ")}`)
    .join("\n");
  return `${header}\n${nodes}`;
}
