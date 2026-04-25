/**
 * /api-docs — interactive OpenAPI viewer for ``GET /api/openapi.json``.
 *
 * Replaces the legacy server-side Swagger UI that was bundled into
 * ``src/media_stack/api/static/swagger-ui*`` and served at
 * ``/api/docs``. That path returned 410 GONE since v1.0.175 because
 * the controller no longer ships HTML; the SPA owns docs viewing now.
 *
 * Implementation: Stoplight Elements <API> web-component (loaded
 * lazily — its Monaco-based editor adds ~2 MB to the bundle and
 * isn't worth eager-loading on every page). The component is mounted
 * with ``router="hash"`` so its internal URL routing doesn't fight
 * Tanstack Router's basepath rewriting (the SPA mounts at
 * ``/app/<slug>/`` in production — see ``ui/src/App.tsx``).
 */
import { createRoute } from "@tanstack/react-router";
import { Suspense, lazy } from "react";
import { Route as RootRoute } from "@/routes/__root";

// Lazy-load both the component and its CSS so the Stoplight chunk
// only ships to users who navigate to /api-docs. Vite splits this
// into its own chunk (see ``manualChunks`` in vite.config.ts).
const StoplightApi = lazy(async () => {
  const [{ API }] = await Promise.all([
    import("@stoplight/elements"),
    import("@stoplight/elements/styles.min.css"),
  ]);
  return { default: API };
});

function ApiDocsPage() {
  return (
    <div className="h-[calc(100vh-3.5rem)] w-full overflow-hidden bg-bg-0">
      <Suspense
        fallback={
          <div className="flex h-full w-full items-center justify-center text-fg-muted">
            Loading API reference…
          </div>
        }
      >
        <StoplightApi
          apiDescriptionUrl="/api/openapi.json"
          // ``hash`` routing keeps Stoplight's internal navigation
          // out of Tanstack Router's path-prefix scope. The viewer
          // appends ``#/operations/<id>`` for deep-links into a
          // specific operation, which survives our basepath
          // because hash fragments aren't part of the path.
          router="hash"
          layout="sidebar"
          tryItCredentialsPolicy="same-origin"
        />
      </Suspense>
    </div>
  );
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: "/api-docs",
  component: ApiDocsPage,
});
