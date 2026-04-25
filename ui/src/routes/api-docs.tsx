/**
 * /api-docs — interactive OpenAPI viewer for ``GET /api/openapi.json``.
 *
 * Replaces the legacy server-side Swagger UI that was bundled into
 * ``src/media_stack/api/static/swagger-ui*`` and served at
 * ``/api/docs``. That path returned 410 GONE since v1.0.175 because
 * the controller no longer ships HTML; the SPA owns docs viewing now.
 *
 * Implementation: Stoplight Elements rendered via its **web-component**
 * build (``<elements-api>``). The React component build
 * (``import {API} from '@stoplight/elements'``) was tried first but
 * its bundle has un-transformed CommonJS ``require()`` calls
 * (``require("util")``, ``require("prismjs/components/...")``) that
 * crash in the browser as "require is not defined". The
 * ``web-components.min.js`` artifact Stoplight ships is pre-built
 * for browsers and side-steps the issue entirely.
 *
 * The script and CSS are loaded once per session, on demand, via
 * ``?url`` imports so Vite hashes them into the dist/assets/ tree
 * (no CDN reliance — this works in air-gapped clusters too).
 */
import { createRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import stoplightCssUrl from "@stoplight/elements/styles.min.css?url";
import stoplightScriptUrl from "@stoplight/elements/web-components.min.js?url";
import { Route as RootRoute } from "@/routes/__root";

// Module-level memoization: once the script + CSS are loaded the
// custom element is registered globally. Re-mounting the route
// shouldn't re-fetch.
let elementsLoadedPromise: Promise<void> | null = null;

function loadStoplightAssets(): Promise<void> {
  if (elementsLoadedPromise) return elementsLoadedPromise;
  elementsLoadedPromise = new Promise<void>((resolve, reject) => {
    // CSS — link tag, no load-completion guarantee but the
    // stylesheet only matters for the rendered element so any
    // late paint is fine.
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = stoplightCssUrl;
    document.head.appendChild(link);

    // Script — must complete before <elements-api> mounts or the
    // browser will treat the unknown tag as an HTMLUnknownElement
    // and silently render nothing.
    const script = document.createElement("script");
    script.src = stoplightScriptUrl;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("Stoplight Elements failed to load"));
    document.head.appendChild(script);
  });
  return elementsLoadedPromise;
}

function ApiDocsPage() {
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    loadStoplightAssets()
      .then(() => {
        if (!cancelled) setReady(true);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // We render <elements-api> as raw HTML once the script is loaded.
  // React 19 supports custom elements but TS doesn't ship JSX types
  // for them; using dangerouslySetInnerHTML avoids needing a JSX
  // type augmentation for a single tag.
  //
  // ``layout="responsive"`` flips between the three-pane sidebar
  // form (wide screens) and the single-column stacked form (narrow)
  // so the AppShell's 240px left rail doesn't squash the operations
  // panel + Try-It column on a typical 1280px laptop.
  useEffect(() => {
    if (!ready || !containerRef.current) return;
    containerRef.current.innerHTML =
      '<elements-api ' +
      'apiDescriptionUrl="/api/openapi.json" ' +
      'router="hash" ' +
      'layout="responsive" ' +
      'tryItCredentialsPolicy="same-origin" ' +
      'style="display:block;height:100%;width:100%;min-height:100%"></elements-api>';
  }, [ready]);

  if (error) {
    return (
      <div className="flex h-[80dvh] w-full items-center justify-center text-danger">
        Failed to load API docs: {error}
      </div>
    );
  }

  // ``100dvh`` (dynamic viewport) excludes the mobile URL bar,
  // matching what the user actually sees. We subtract a generous
  // 7rem for the AppShell's top chrome — banners (UpgradeBanner,
  // TriggeredBanner) plus TopBar can stack to ~6rem; the extra
  // 1rem of padding keeps the bottom edge from kissing the
  // BottomNav on mobile.
  //
  // The wrapper also forces ``min-w-0`` so the flex parent
  // (AppShell main) lets us shrink past the children's preferred
  // width — without it Stoplight's longest operation IDs cause
  // horizontal overflow that triggers a page-level scrollbar.
  return (
    <div className="flex h-[calc(100dvh-7rem)] min-w-0 w-full flex-col overflow-hidden bg-bg-0">
      {!ready && (
        <div className="flex h-full w-full items-center justify-center text-fg-muted">
          Loading API reference…
        </div>
      )}
      <div ref={containerRef} className="min-h-0 flex-1 w-full" />
    </div>
  );
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: "/api-docs",
  component: ApiDocsPage,
});
