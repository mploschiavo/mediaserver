// UUID v4-ish key generator for `Idempotency-Key` headers.
// We accept a non-RFC4122 fallback under jsdom/happy-dom test envs that
// pre-date `crypto.randomUUID` — the controller treats the header as an
// opaque string, so what matters is uniqueness, not strict v4 layout.

export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return Array.from({ length: 16 }, () => Math.floor(Math.random() * 256))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
