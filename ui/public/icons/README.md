# UI app icons

The icons in this directory are **placeholders**. Before shipping a
real release, replace them with rasterized PNGs at the documented
sizes.

## Generate from the SVG sources

```
pnpm i -D sharp
node scripts/generate-png-icons.mjs
```

Sources:
- `icon.svg`        — 512x512 base, used for `any` purpose icons
- `icon-mask.svg`   — 512x512 maskable variant (content in inner 80%)
- `shortcut-*.svg`  — 96x96 quick-action icons (referenced by the manifest's `shortcuts`)
