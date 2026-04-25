#!/usr/bin/env node
// Run with: pnpm dlx sharp-cli ...
// Or: pnpm i -D sharp && node scripts/generate-png-icons.mjs
import sharp from "sharp";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "../public/icons");
const tasks = [
  { svg: "icon.svg", out: "icon-192.png", size: 192 },
  { svg: "icon.svg", out: "icon-512.png", size: 512 },
  { svg: "icon-mask.svg", out: "icon-mask-512.png", size: 512 },
  { svg: "shortcut-mi.svg", out: "shortcut-mi.png", size: 96 },
  { svg: "shortcut-logs.svg", out: "shortcut-logs.png", size: 96 },
  { svg: "shortcut-rec.svg", out: "shortcut-rec.png", size: 96 },
];
for (const t of tasks) {
  const svg = readFileSync(resolve(root, t.svg));
  await sharp(svg)
    .resize(t.size, t.size)
    .png({ compressionLevel: 9 })
    .toFile(resolve(root, t.out));
  console.log(`generated ${t.out}`);
}
