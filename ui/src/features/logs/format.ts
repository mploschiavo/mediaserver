// Pure helpers for the Logs feature. Kept here (not in /lib) so they
// stay private to the feature surface and trivially tree-shakable.
//
// Three responsibilities:
//   1. Pull a leading timestamp out of a raw container log line.
//   2. Hash a source name into one of 8 stable OKLCH tones, so the
//      "controller" chip looks identical every time the page renders.
//   3. Split a line into [match | non-match] segments for the search
//      highlighter. Both substring and regex modes share one path.

/**
 * Match the leading timestamp prefix that container logs typically
 * emit. Two shapes are supported:
 *   - ISO-ish with offset: `[2026-04-07T12:00:01+0000]`
 *   - SQL-ish space-sep:   `[2026-04-07 12:00:01]`
 * Anything else falls through and the column renders "—".
 */
const TS_RE =
  /^\[?(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:?\d{2}|Z)?)\]?\s*/;

/**
 * Extract a timestamp prefix (if present) from a raw line. Returns
 * the iso-ish timestamp string and the remainder of the line with
 * the prefix stripped, so the message column doesn't double-print
 * the same data the timestamp column already shows.
 */
export function extractTimestamp(raw: string): {
  ts: string | null;
  rest: string;
} {
  const m = TS_RE.exec(raw);
  if (!m) return { ts: null, rest: raw };
  return { ts: m[1] ?? null, rest: raw.slice(m[0].length) };
}

/** Stable, deterministic 32-bit hash (FNV-1a). Pure; no Math.random. */
function fnv1a(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    // Equivalent to `h * 0x01000193` mod 2^32, but avoids float drift.
    h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
  }
  return h >>> 0;
}

// Eight OKLCH tones spaced ~45deg around the hue circle. Picked to
// stay visually distinct on both light and dark themes while honoring
// the design system's "muted, never neon" feel — chroma stays at
// 0.12 and lightness at 0.62 for AA contrast on a `--bg-2` background.
const SOURCE_TONES = [
  // hue:    text                            border + background mix
  { fg: "oklch(0.62 0.12 25)" }, // red
  { fg: "oklch(0.62 0.12 70)" }, // orange
  { fg: "oklch(0.62 0.12 115)" }, // yellow-green
  { fg: "oklch(0.62 0.12 160)" }, // green
  { fg: "oklch(0.62 0.12 205)" }, // teal
  { fg: "oklch(0.62 0.12 250)" }, // blue
  { fg: "oklch(0.62 0.12 295)" }, // purple
  { fg: "oklch(0.62 0.12 340)" }, // pink
] as const;

/** Number of distinct color tones in the source-color palette. */
export const SOURCE_TONE_COUNT = SOURCE_TONES.length;

/**
 * Map a source name to a stable inline-style chip color. Same name
 * always returns the same tone. Used by `LogsTable` to color-code
 * the source column when many sources stream in at once.
 */
export function hashSource(name: string): { fg: string } {
  const idx = fnv1a(name) % SOURCE_TONE_COUNT;
  return SOURCE_TONES[idx]!;
}

/** Segment of a line, used by the highlighter. */
export interface HighlightSegment {
  text: string;
  match: boolean;
}

/**
 * Parse the user's search input.
 *
 *   - `/foo/`     -> regex `/foo/` (case-sensitive)
 *   - `/foo/i`    -> regex `/foo/i` (case-insensitive)
 *   - `foo`       -> substring match (case-insensitive by default)
 *
 * Closing `/` is OPTIONAL — `/foo` is also treated as a regex so
 * the operator doesn't have to remember to type the trailing slash
 * mid-edit. Invalid regexes fall back to substring on the literal.
 */
export interface ParsedSearch {
  /** True when the input matched any non-empty token. */
  active: boolean;
  /** A predicate that returns true if the line text matches. */
  test: (text: string) => boolean;
  /** Highlight a string into matched/non-matched segments. */
  split: (text: string) => HighlightSegment[];
}

export function parseSearch(input: string): ParsedSearch {
  const q = input.trim();
  if (q === "") {
    return {
      active: false,
      test: () => true,
      split: (text) => [{ text, match: false }],
    };
  }
  // Regex mode: leading `/` (closing `/` optional, optional flags).
  let substringNeedle = q;
  if (q.startsWith("/")) {
    const m = /^\/(.*?)(?:\/([gimsuy]*))?$/.exec(q);
    const body = m?.[1] ?? q.slice(1);
    const flags = m?.[2] ?? "";
    if (body.length > 0) {
      try {
        const re = new RegExp(body, flags.includes("g") ? flags : flags + "g");
        return {
          active: true,
          test: (text) => {
            re.lastIndex = 0;
            return re.test(text);
          },
          split: (text) => splitByRegex(text, re),
        };
      } catch {
        // Invalid regex — fall through to substring on the body, not
        // the `/.../` form. This way a typo like `/foo[/` still finds
        // the literal token "foo[" the operator was reaching for.
        substringNeedle = body;
      }
    }
  }
  const needle = substringNeedle.toLowerCase();
  return {
    active: true,
    test: (text) => text.toLowerCase().includes(needle),
    split: (text) => splitBySubstring(text, substringNeedle),
  };
}

function splitBySubstring(text: string, q: string): HighlightSegment[] {
  if (!q) return [{ text, match: false }];
  const lower = text.toLowerCase();
  const needle = q.toLowerCase();
  const out: HighlightSegment[] = [];
  let i = 0;
  while (i < text.length) {
    const found = lower.indexOf(needle, i);
    if (found < 0) {
      out.push({ text: text.slice(i), match: false });
      break;
    }
    if (found > i) out.push({ text: text.slice(i, found), match: false });
    out.push({ text: text.slice(found, found + needle.length), match: true });
    i = found + needle.length;
    if (needle.length === 0) break;
  }
  return out.length ? out : [{ text, match: false }];
}

function splitByRegex(text: string, re: RegExp): HighlightSegment[] {
  const out: HighlightSegment[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  re.lastIndex = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push({ text: text.slice(last, m.index), match: false });
    const matched = m[0];
    if (matched.length === 0) {
      // Zero-width — bump past so we don't spin.
      re.lastIndex = m.index + 1;
      continue;
    }
    out.push({ text: matched, match: true });
    last = m.index + matched.length;
  }
  if (last < text.length) out.push({ text: text.slice(last), match: false });
  return out.length ? out : [{ text, match: false }];
}
