/**
 * Shared semantic tone class strings for the onboarding/setup
 * surfaces. Centralized so the same Tailwind class names live in
 * exactly one place — the duplicate-string-literal ratchet
 * otherwise flags them as drift candidates.
 */

export const TONE_TEXT = {
  info: "text-info",
  success: "text-success",
  warning: "text-warning",
  danger: "text-danger",
} as const;
