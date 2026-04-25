import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";

/**
 * PWA manifest/icon contract test.
 *
 * Every URL referenced in the production-built `dist/manifest.webmanifest`
 * must resolve to a real file on disk, with the correct dimensions.
 * Catches missing or mis-sized PWA icons before they hit production.
 *
 * If `dist/manifest.webmanifest` does not exist (e.g. `vite build` has not
 * been run), the suite is skipped rather than failed — running unit tests
 * shouldn't require a fresh production bundle.
 */

interface ManifestIcon {
  src: string;
  sizes: string;
  type?: string;
  purpose?: string;
}

interface ManifestShortcut {
  name: string;
  short_name?: string;
  url: string;
  icons?: ManifestIcon[];
}

interface WebManifest {
  name?: string;
  short_name?: string;
  start_url?: string;
  display?: string;
  theme_color?: string;
  background_color?: string;
  icons?: ManifestIcon[];
  shortcuts?: ManifestShortcut[];
}

// `__dirname` equivalent for Vitest (which runs ESM). The project root is
// two levels up from `src/lib/`.
const UI_ROOT = path.resolve(__dirname, "..", "..");
const DIST_DIR = path.join(UI_ROOT, "dist");
const MANIFEST_PATH = path.join(DIST_DIR, "manifest.webmanifest");

const ALLOWED_ROUTES = new Set<string>([
  "/",
  "/media-integrity",
  "/logs",
  "/ops",
  "/routing",
  "/webhooks",
  "/users",
  "/me",
  "/profile",
  "/settings",
  "/content",
]);

/**
 * Parse a PNG's IHDR chunk to recover (width, height).
 *
 * PNG layout:
 *   bytes 0..7   = signature (89 50 4E 47 0D 0A 1A 0A)
 *   bytes 8..11  = IHDR chunk length (always 13)
 *   bytes 12..15 = "IHDR"
 *   bytes 16..19 = width  (uint32 BE)
 *   bytes 20..23 = height (uint32 BE)
 */
function readPngDimensions(filePath: string): { width: number; height: number } {
  const fd = fs.openSync(filePath, "r");
  try {
    const buf = Buffer.alloc(24);
    const bytesRead = fs.readSync(fd, buf, 0, 24, 0);
    if (bytesRead < 24) {
      throw new Error(
        `PNG ${filePath} is truncated: only ${bytesRead} bytes available`,
      );
    }
    const sig = buf.subarray(0, 8).toString("hex");
    if (sig !== "89504e470d0a1a0a") {
      throw new Error(
        `File ${filePath} is not a PNG (signature ${sig})`,
      );
    }
    const ihdr = buf.subarray(12, 16).toString("ascii");
    if (ihdr !== "IHDR") {
      throw new Error(
        `File ${filePath} missing IHDR chunk (got "${ihdr}")`,
      );
    }
    const width = buf.readUInt32BE(16);
    const height = buf.readUInt32BE(20);
    return { width, height };
  } finally {
    fs.closeSync(fd);
  }
}

function parseDeclaredSize(sizes: string): { width: number; height: number } {
  const match = /^(\d+)x(\d+)$/.exec(sizes);
  if (!match) {
    throw new Error(`Unparseable "sizes" value: "${sizes}"`);
  }
  return { width: Number(match[1]), height: Number(match[2]) };
}

function resolveDistPath(src: string): string {
  // Manifest URLs are absolute (start with `/`), but on disk they live
  // under `dist/`. Strip the leading slash before joining.
  const stripped = src.startsWith("/") ? src.slice(1) : src;
  return path.join(DIST_DIR, stripped);
}

const manifestExists = fs.existsSync(MANIFEST_PATH);

describe("PWA manifest contract", () => {
  if (!manifestExists) {
    it.skip(
      `dist/manifest.webmanifest not found at ${MANIFEST_PATH} — run \`vite build\` first`,
      () => {},
    );
    return;
  }

  const raw = fs.readFileSync(MANIFEST_PATH, "utf8");
  let manifest: WebManifest;
  try {
    manifest = JSON.parse(raw) as WebManifest;
  } catch (err) {
    throw new Error(
      `dist/manifest.webmanifest is not valid JSON: ${(err as Error).message}`,
    );
  }

  it("declares all required top-level fields with non-empty values", () => {
    const requiredKeys = [
      "name",
      "short_name",
      "start_url",
      "display",
      "theme_color",
      "background_color",
    ] as const;
    for (const key of requiredKeys) {
      const value = manifest[key];
      expect(
        typeof value === "string" && value.length > 0,
        `manifest.${key} must be a non-empty string (got ${JSON.stringify(value)})`,
      ).toBe(true);
    }
  });

  it("declares at least one icon", () => {
    expect(Array.isArray(manifest.icons)).toBe(true);
    expect((manifest.icons ?? []).length).toBeGreaterThan(0);
  });

  describe("manifest.icons[]", () => {
    const icons = manifest.icons ?? [];
    for (const icon of icons) {
      const label = `${icon.src} (${icon.sizes}${
        icon.purpose ? `, purpose=${icon.purpose}` : ""
      })`;

      it(`${label} resolves to a real file with matching dimensions`, () => {
        const filePath = resolveDistPath(icon.src);
        expect(
          fs.existsSync(filePath),
          `icon file missing on disk: ${filePath}`,
        ).toBe(true);

        const declared = parseDeclaredSize(icon.sizes);
        const actual = readPngDimensions(filePath);
        expect(
          actual.width,
          `${icon.src} width mismatch: declared ${declared.width}, actual ${actual.width}`,
        ).toBe(declared.width);
        expect(
          actual.height,
          `${icon.src} height mismatch: declared ${declared.height}, actual ${actual.height}`,
        ).toBe(declared.height);
      });
    }
  });

  describe("manifest.shortcuts[]", () => {
    const shortcuts = manifest.shortcuts ?? [];

    for (const shortcut of shortcuts) {
      it(`shortcut "${shortcut.name}" url is a relative path on the route allow-list`, () => {
        expect(
          typeof shortcut.url === "string" && shortcut.url.startsWith("/"),
          `shortcut "${shortcut.name}" url must start with "/" (got ${JSON.stringify(shortcut.url)})`,
        ).toBe(true);

        // Strip query string so `/media-integrity?action=reconcile`
        // resolves to the `/media-integrity` route.
        const pathOnly = shortcut.url.split("?")[0]!.split("#")[0]!;
        expect(
          ALLOWED_ROUTES.has(pathOnly),
          `shortcut "${shortcut.name}" url "${shortcut.url}" (path "${pathOnly}") not in route allow-list: ${[...ALLOWED_ROUTES].join(", ")}`,
        ).toBe(true);
      });

      for (const icon of shortcut.icons ?? []) {
        const label = `shortcut "${shortcut.name}" icon ${icon.src} (${icon.sizes})`;
        it(`${label} resolves to a real file with matching dimensions`, () => {
          const filePath = resolveDistPath(icon.src);
          expect(
            fs.existsSync(filePath),
            `shortcut icon missing on disk: ${filePath}`,
          ).toBe(true);

          const declared = parseDeclaredSize(icon.sizes);
          const actual = readPngDimensions(filePath);
          expect(
            actual.width,
            `${icon.src} width mismatch: declared ${declared.width}, actual ${actual.width}`,
          ).toBe(declared.width);
          expect(
            actual.height,
            `${icon.src} height mismatch: declared ${declared.height}, actual ${actual.height}`,
          ).toBe(declared.height);
        });
      }
    }
  });
});
