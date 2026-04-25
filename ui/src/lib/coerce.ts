/**
 * Defensive coercion helpers for loosely-typed API payloads.
 *
 * The controller's OpenAPI uses `additionalProperties: true` on
 * many response schemas, so the same field has shifted shape
 * across versions: an array on one build, an object map on the
 * next, a string on the third. The TypeScript types we generate
 * from the spec optimistically declare arrays, but runtime can
 * surprise us. These helpers contain the surprise.
 *
 * Use these everywhere a UI panel iterates a payload field:
 *
 *   const rows = asArray<MyShape>(query.data?.items);
 *   rows.map(...) // safe, even if `items` came back as an object
 */

/**
 * Coerce an unknown value into a readonly array. Non-arrays
 * (including object maps, strings, null, undefined) collapse to
 * an empty array. Use this in render paths so a stray non-array
 * payload renders an empty state instead of crashing the route.
 */
// TypeScript function overloads — eslint's `no-redeclare` flags them
// as if they were duplicate JS declarations. They aren't; the
// overload signatures vanish at compile time.
/* eslint-disable no-redeclare */
export function asArray<T>(
  value: readonly T[] | undefined | null,
): readonly T[];
export function asArray<T = unknown>(value: unknown): readonly T[];
export function asArray<T>(value: unknown): readonly T[] {
  return Array.isArray(value) ? (value as readonly T[]) : [];
}
/* eslint-enable no-redeclare */

/**
 * Coerce an unknown value into a string-keyed record. Arrays,
 * primitives, null, and undefined collapse to an empty record.
 * Use before `Object.entries(...)` on optional `additionalProperties`
 * payloads.
 */
export function asObjectMap(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
