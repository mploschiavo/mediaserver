/// <reference lib="webworker" />

/**
 * Custom service worker for the dashboard PWA.
 *
 * Replaces the auto-generated Workbox SW (``generateSW`` mode) with
 * an ``injectManifest`` build so we can pull the navigation
 * denylist from ``GET /sw-config.json`` at install time. The
 * routing engine becomes the single source of truth for which
 * paths the SW should hijack vs pass through — operators can
 * rename the dashboard mount or add a sister app without
 * rebuilding the bundle.
 *
 * Behavior:
 *   1. ``install`` event fetches ``/sw-config.json`` (best-effort
 *      with a 3s timeout; falls back to safe defaults). Compiles
 *      the denylist regexes once.
 *   2. ``activate`` event takes control of clients immediately so
 *      the new config applies on next navigation.
 *   3. ``fetch`` event:
 *      a. Precached static assets from the build manifest → served
 *         from cache.
 *      b. Navigation requests → ``shouldServeSpaShell()`` decides
 *         (SPA fallback vs. network). Helper is unit-tested.
 *      c. Everything else → network (no caching for ``/api/*``).
 *
 * The decision logic lives in ``sw-config.ts`` as pure functions so
 * tests don't need a real ServiceWorkerGlobalScope.
 */

import { precacheAndRoute, createHandlerBoundToURL } from "workbox-precaching";
import { NavigationRoute, registerRoute } from "workbox-routing";
import { CacheFirst, NetworkOnly } from "workbox-strategies";
import { ExpirationPlugin } from "workbox-expiration";
import {
  compileDenylist,
  fetchSwConfig,
  SW_CONFIG_DEFAULTS,
  shouldServeSpaShell,
  type SwConfig,
} from "./sw-config";

// Workbox replaces this at build time with the precache manifest.
// MUST be written as `self.__WB_MANIFEST` — workbox-build's injection
// regex looks for that exact form (a bare `__WB_MANIFEST` reference is
// not detected and the post-build step throws "Unable to find a place
// to inject the manifest"). The shape is workbox-internal — typed
// loosely as ``unknown`` to keep the strict-TS gate (no ``any``)
// happy; ``precacheAndRoute`` forwards the value verbatim.
declare const self: ServiceWorkerGlobalScope & {
  __WB_MANIFEST: unknown;
};
precacheAndRoute(
  self.__WB_MANIFEST as Parameters<typeof precacheAndRoute>[0],
);

// Module-scope cache of the live config + compiled regexes. Set
// during the install event and re-read on each fetch handler call.
// Falling back to defaults guarantees the SW can still answer
// navigations even if the controller is unreachable on first
// install (offline-first install scenarios).
let swConfig: SwConfig = SW_CONFIG_DEFAULTS;
let denylist = compileDenylist(SW_CONFIG_DEFAULTS.denylist_patterns);

self.addEventListener("install", (event: ExtendableEvent) => {
  event.waitUntil(
    (async () => {
      // Race the controller fetch against a 3s timeout — first-pod
      // installs sometimes can't reach the controller during the SW
      // window. Defaults are safe enough for the offline path.
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 3000);
      try {
        swConfig = await fetchSwConfig((url, init) =>
          fetch(url, { ...init, signal: controller.signal }),
        );
      } finally {
        clearTimeout(timer);
      }
      denylist = compileDenylist(swConfig.denylist_patterns);
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener("activate", (event: ExtendableEvent) => {
  event.waitUntil(self.clients.claim());
});

// On every navigation, re-read the cached config + denylist (set at
// install) and decide whether to serve the SPA shell. A request
// outside our basepath, OR matching a denylist regex, falls through
// to Workbox's default network handling.
const navigationHandler = createHandlerBoundToURL("/index.html");
registerRoute(
  new NavigationRoute(
    async (params) => {
      const { request, url } = params;
      const navUrl = url instanceof URL ? url : new URL(request.url);
      if (shouldServeSpaShell(navUrl, swConfig, denylist)) {
        return navigationHandler(params);
      }
      // Pass through to the network so Envoy can route to whichever
      // sister app owns this path.
      return fetch(request);
    },
  ),
);

// Never cache /api/* — session-tied data.
registerRoute(
  ({ url }) => url.pathname.startsWith("/api/"),
  new NetworkOnly(),
);

// Cache-first for woff2/woff/ttf/eot assets — long-lived, content-
// addressed by hash.
registerRoute(
  ({ url }) => /\.(?:woff2|woff|ttf|eot)$/.test(url.pathname),
  new CacheFirst({
    cacheName: `media-stack-fonts`,
    plugins: [
      new ExpirationPlugin({
        maxEntries: 16,
        maxAgeSeconds: 60 * 60 * 24 * 365,
      }),
    ],
  }),
);

// Cache the Geist fonts loaded from jsdelivr.
registerRoute(
  ({ url }) => url.origin === "https://cdn.jsdelivr.net",
  new CacheFirst({
    cacheName: `media-stack-cdn-fonts`,
    plugins: [
      new ExpirationPlugin({
        maxEntries: 16,
        maxAgeSeconds: 60 * 60 * 24 * 365,
      }),
    ],
  }),
);
