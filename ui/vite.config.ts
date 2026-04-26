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
      // The dashboard is admin-only and behind auth; keep the SW
      // strategy lean: cache the app shell + static assets, never
      // cache /api/* (that's session-tied data).
      workbox: {
        // Version-stamp the precache + runtime cache names so a new
        // build registers under a fresh cache identity. Workbox's
        // standard activate handler then deletes any cache whose
        // name doesn't match the current set, killing stale HTML/JS
        // automatically on the next SW activation.
        cacheId: `media-stack-${BUILD_VERSION}`,
        globPatterns: ["**/*.{js,css,html,ico,png,svg,woff2}"],
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [/^\/api\//],
        runtimeCaching: [
          {
            urlPattern: /\/api\/.*/,
            handler: "NetworkOnly", // never cache API
          },
          {
            urlPattern: /\.(?:woff2|woff|ttf|eot)$/,
            handler: "CacheFirst",
            options: {
              cacheName: `media-stack-fonts-${BUILD_VERSION}`,
              expiration: {
                maxEntries: 16,
                maxAgeSeconds: 60 * 60 * 24 * 365,
              },
            },
          },
          {
            // Cache the Geist fonts loaded from jsdelivr.
            urlPattern: /^https:\/\/cdn\.jsdelivr\.net\/.*$/,
            handler: "CacheFirst",
            options: {
              cacheName: `media-stack-cdn-fonts-${BUILD_VERSION}`,
              expiration: {
                maxEntries: 16,
                maxAgeSeconds: 60 * 60 * 24 * 365,
              },
            },
          },
        ],
      },
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
      // Surface the PWA prompt at controlled times via the registration
      // helper instead of the plugin's auto-prompt — luxury feel beats
      // surprise dialogs.
      injectManifest: undefined,
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
