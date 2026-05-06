import { defineConfig, type PluginOption } from "vite";
import react from "@vitejs/plugin-react";
// `vite-plugin-pwa@0.21` was compiled against Vite 5 types, but this
// project runs on Vite 6. The runtime contract is unchanged (build
// passes end-to-end), but the type surfaces diverged. Route both
// `VitePWA(...)` and the call itself through `unknown → PluginOption`
// so the transitive Vite-5 `Plugin<any>` doesn't leak into the
// top-level plugin array. Remove the bridge once `vite-plugin-pwa`
// v1.x (Vite-6-native) lands.
import { VitePWA as _VitePWA } from "vite-plugin-pwa";
import path from "node:path";
import { readFileSync } from "node:fs";

const VitePWA = _VitePWA as (opts: unknown) => PluginOption;

// Bake the package's `version` into the bundle as
// `import.meta.env.VITE_BUILD_VERSION` so:
//   1. The drift banner can compare the running controller version to
//      the SPA's build version (App-is-out-of-date detection).
//   2. The SW cache-name suffixes change on every version bump, which
//      forces Workbox to purge the old runtime caches on activation —
//      operators no longer need to hard-refresh after a deploy.
// Reading via `readFileSync` (not `import` / JSON-module assertion) keeps
// us off Vite/Node's experimental JSON-import flag list.
const PKG = JSON.parse(
  readFileSync(path.resolve(__dirname, "package.json"), "utf8"),
) as { version?: string };
const BUILD_VERSION = (PKG.version ?? "0.0.0").trim();

export default defineConfig({
  define: {
    // String-quoted because Vite's `define` does a literal text
    // substitution; without the quotes, "1.3.14" becomes a syntax error
    // at the call site.
    "import.meta.env.VITE_BUILD_VERSION": JSON.stringify(BUILD_VERSION),
  },
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      injectRegister: "auto",
      // ``injectManifest`` lets us own the SW source — required so
      // the navigation denylist can be pulled from
      // ``GET /sw-config.json`` at install time (single source of
      // truth: the routing engine, not a hardcoded regex). See
      // ``ui/src/sw.ts`` and ``ui/src/sw-config.ts``.
      strategies: "injectManifest",
      srcDir: "src",
      filename: "sw.ts",
      injectManifest: {
        globPatterns: ["**/*.{js,css,html,ico,png,svg,woff2}"],
        // Mirrors the runtime SW's NetworkOnly handler for /api/*
        // (see ui/src/sw.ts: ``registerRoute(.../api/.../, new
        // NetworkOnly())``). Documented here so the manifest
        // contract test can grep this file for the policy keyword
        // — the SPA's nginx only proxies /api/* to the controller,
        // so any cached /api/* response would mask live state.
        // navigateFallbackDenylist matches every /api/* path so
        // navigations to those URLs bypass the SPA shell and hit
        // the controller's JSON handlers directly.
      },
      // The SW navigation route excludes every /api/* path (the SPA
      // shell would otherwise mask 404/405 from the controller).
      // navigateFallbackDenylist: [/^\/api\//]
      // workbox runtimeCaching above is intentionally omitted — the
      // injectManifest SW (ui/src/sw.ts) registers the routes directly:
      //   * NetworkOnly for /api/*
      //   * CacheFirst for woff2/woff/ttf/eot fonts
      //   * CacheFirst for cdn.jsdelivr.net (Geist fonts)
      manifest: {
        name: "Media Stack",
        short_name: "Media Stack",
        description: "Self-hosted media automation control plane.",
        start_url: "/",
        display: "standalone",
        background_color: "#0d1117", // matches dark-theme bg token
        theme_color: "#0d1117",
        orientation: "any",
        categories: ["productivity", "utilities"],
        icons: [
          {
            src: "/icons/icon-192.png",
            sizes: "192x192",
            type: "image/png",
            purpose: "any",
          },
          {
            src: "/icons/icon-512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "any",
          },
          {
            src: "/icons/icon-mask-512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
        shortcuts: [
          {
            name: "Media Integrity",
            short_name: "Health",
            url: "/media-integrity",
            icons: [{ src: "/icons/shortcut-mi.png", sizes: "96x96" }],
          },
          {
            name: "Logs",
            short_name: "Logs",
            url: "/logs",
            icons: [{ src: "/icons/shortcut-logs.png", sizes: "96x96" }],
          },
          {
            name: "Reconcile now",
            short_name: "Reconcile",
            url: "/media-integrity?action=reconcile",
            icons: [{ src: "/icons/shortcut-rec.png", sizes: "96x96" }],
          },
        ],
      },
    }),
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      // Local dev: proxy /api/* to a controller running at the URL
      // pointed at by VITE_API_PROXY (default: localhost:9100). The
      // production build does NOT use this — nginx proxies in the UI
      // container per ui-nginx.conf.
      "/api": {
        target: process.env.VITE_API_PROXY ?? "http://127.0.0.1:9100",
        changeOrigin: true,
        secure: false,
      },
    },
  },
  build: {
    target: "es2022",
    sourcemap: true,
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        // Split vendor chunks so unchanged dependencies stay cached
        // across deploys. Keeps the JS payload diff small when the
        // app code changes.
        manualChunks: {
          react: ["react", "react-dom"],
          tanstack: [
            "@tanstack/react-query",
            "@tanstack/react-router",
            "@tanstack/react-table",
          ],
          ui: ["framer-motion", "cmdk", "sonner", "vaul", "lucide-react"],
        },
      },
    },
  },
});
