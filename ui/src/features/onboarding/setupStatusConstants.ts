/**
 * Shared status / role string constants for the onboarding hero
 * card and checklist. Centralizing avoids the "completed" vs
 * "complete" drift the duplicate-literal ratchet exists to catch.
 *
 * Use a `const` object + `as const` rather than a TS `enum`: enums
 * are the only TS construct that emits runtime JS (not just type-
 * erased), don't tree-shake well, and are discouraged by every
 * mainstream TS style guide for new code. The pattern below gives
 * the same type-narrowing safety with no runtime overhead.
 */

export const SetupStatus = {
  WarmingUp: "warming_up",
  Queued: "queued",
  Starting: "starting",
  Running: "running",
  Ok: "ok",
  Completed: "completed",
  Complete: "complete",
  CompleteWithWarnings: "complete_with_warnings",
  Skipped: "skipped",
  Cancelled: "cancelled",
  TimedOut: "timed_out",
  Timeout: "timeout",
  Failed: "failed",
  Error: "error",
} as const;

export type SetupStatusValue = (typeof SetupStatus)[keyof typeof SetupStatus];

export const PROGRESSBAR_ROLE = "progressbar";
