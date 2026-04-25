import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

/**
 * Manifest contract test.
 *
 * vite-plugin-pwa generates the runtime manifest at build time, so
 * during unit tests we can't read a built `manifest.webmanifest`. We
 * instead assert the source-of-truth: the plugin is wired into
 * vite.config.ts and the manifest object embedded there has the
 * fields the install prompt needs.
 */
describe("PWA manifest", () => {
  const configPath = resolve(__dirname, "../../vite.config.ts");
  const config = readFileSync(configPath, "utf8");

  it("imports VitePWA from vite-plugin-pwa", () => {
    expect(config).toMatch(/from\s+["']vite-plugin-pwa["']/);
    expect(config).toMatch(/VitePWA\s*\(/);
  });

  it("declares core manifest identity fields", () => {
    expect(config).toMatch(/name:\s*["']Media Stack["']/);
    expect(config).toMatch(/short_name:\s*["']Media Stack["']/);
    expect(config).toMatch(/start_url:\s*["']\/["']/);
    expect(config).toMatch(/display:\s*["']standalone["']/);
  });

  it("sets theme_color and background_color to the dark-bg token", () => {
    expect(config).toMatch(/theme_color:\s*["']#0d1117["']/);
    expect(config).toMatch(/background_color:\s*["']#0d1117["']/);
  });

  it("declares icons covering 192, 512, and a maskable 512", () => {
    expect(config).toMatch(/\/icons\/icon-192\.png/);
    expect(config).toMatch(/\/icons\/icon-512\.png/);
    expect(config).toMatch(/\/icons\/icon-mask-512\.png/);
    expect(config).toMatch(/purpose:\s*["']maskable["']/);
  });

  it("declares the three luxury-tier shortcuts", () => {
    expect(config).toMatch(/Media Integrity/);
    expect(config).toMatch(/\/media-integrity/);
    expect(config).toMatch(/\/logs/);
    expect(config).toMatch(/\/media-integrity\?action=reconcile/);
  });

  it("never caches /api/* responses", () => {
    expect(config).toMatch(/NetworkOnly/);
    expect(config).toMatch(/navigateFallbackDenylist:\s*\[\s*\/\^\\\/api\\\//);
  });

  it("references all icon source files that are present on disk", () => {
    const iconsDir = resolve(__dirname, "../../public/icons");
    const required = [
      "icon.svg",
      "icon-mask.svg",
      "shortcut-mi.svg",
      "shortcut-logs.svg",
      "shortcut-rec.svg",
      "icon-192.png",
      "icon-512.png",
      "icon-mask-512.png",
      "shortcut-mi.png",
      "shortcut-logs.png",
      "shortcut-rec.png",
    ];
    for (const file of required) {
      expect(() => readFileSync(resolve(iconsDir, file))).not.toThrow();
    }
  });
});
