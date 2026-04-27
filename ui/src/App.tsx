import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createRouter } from "@tanstack/react-router";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { Toaster } from "sonner";
import { useEffect } from "react";
import { ErrorBoundary } from "@/components/layout/ErrorBoundary";
import { ThemeProvider } from "@/components/layout/ThemeProvider";
import { onAuthEvent } from "@/api/client";
import { authPortal } from "@/lib/auth-portal";
import { routeTree } from "@/routeTree";
import { toast } from "sonner";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

// Auto-detect the deployment basepath from the document URL the SPA
// was loaded under. In production Envoy mounts the UI at
// `/app/media-stack-ui/*`; the dev server (vite) serves it at `/`.
//
// Without a basepath, raw `history.pushState`/`replaceState` calls
// that build a URL from `window.location.pathname` would re-feed the
// full prefixed path through the router, which has only bare-path
// routes registered (`/logs`, `/audit-log`, …) — the next match cycle
// would fall to the splat 404. Going through the router with a
// declared basepath keeps every navigation consistent regardless of
// whether the user deep-linked or click-navigated in.
const ROUTER_BASEPATH: string | undefined = (() => {
  if (typeof window === "undefined") return undefined;
  const m = /^(\/app\/[^/]+)\//.exec(window.location.pathname);
  return m?.[1];
})();

const router = createRouter({
  routeTree,
  ...(ROUTER_BASEPATH ? { basepath: ROUTER_BASEPATH } : {}),
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

/**
 * Root composition. Wires the global providers (Query, Router,
 * Theme, Tooltip, Toaster) and hands off to the route tree, which
 * mounts the AppShell at __root.
 */
export function App() {
  // When the controller returns 401, the API client emits
  // `unauthenticated`. Send the user to the Authelia portal so they
  // can sign in. Without this, the SW-cached app shell renders for
  // unauthenticated users and they see empty skeletons instead of a
  // login screen.
  //
  // Guards (both required — earlier versions caused a redirect loop):
  // 1. **Path guard**: do nothing if the browser is already at
  //    `/app/authelia/*`. The portal itself emits 401 for /api/verify
  //    polling; if we redirected on that, the URL would recursively
  //    embed itself as the `rd` query param every cycle, exploding to
  //    multi-megabyte URLs in seconds.
  // 2. **One-shot guard**: only fire one redirect per page load. Once
  //    the redirect is in flight, additional 401s from in-flight
  //    requests must be ignored — otherwise React Query's retry can
  //    fire 2-3 redirects before the navigation actually happens.
  // 3. **No `rd=` param**: rely on Authelia's own redirect-after-login
  //    flow (it remembers the originally-requested URL via its
  //    session). Encoding the current URL into `rd` was the explicit
  //    re-entry vector for the loop and produces no UX benefit when
  //    the user hits the dashboard root anyway.
  useEffect(() => {
    let redirected = false;
    const path = window.location.pathname;
    const host = window.location.hostname;
    // Already on the auth portal (either as a path-prefix legacy mount
    // OR as a dedicated subdomain like ``auth.<base>``) — never
    // re-redirect or we'd ping-pong.
    const isAuthPath =
      path.startsWith("/app/authelia") ||
      path.startsWith("/api/verify") ||
      host.startsWith("auth.");

    const redirectToLogin = () => {
      if (redirected || isAuthPath) return;
      redirected = true;
      // Brief, non-blocking toast before the hard redirect — operator
      // sees what happened instead of jarring nav. 1.5s is short
      // enough nobody waits, long enough that the toast registers.
      try {
        toast.warning("Session expired — redirecting to sign in…", {
          duration: 1500,
        });
      } catch {
        // Toaster may not be mounted on the auth path; redirect is
        // the load-bearing step.
      }
      // Cookie clearing via document.cookie is a no-op for the
      // ``authelia_session`` cookie because it's HttpOnly — the JS
      // line below can't touch it. Kept only for the legacy
      // ``authelia_session_remember`` (non-HttpOnly persistent
      // cookie) which CAN be cleared client-side; everything else
      // relies on Envoy's ext_authz check of the live session
      // store (POST /api/logout already invalidated it).
      try {
        document.cookie =
          "authelia_session_remember=; Path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT";
      } catch {
        // best-effort
      }
      // Use the dedicated Authelia portal hostname (auth.<base>) for
      // the redirect target. The earlier ``/app/authelia/?rd=…``
      // pattern routed through the *same* host as the dashboard,
      // which the Lua prefix-patch then mangled — operators got stuck
      // at ``/app/authelia/<some-route>`` instead of seeing the login
      // form. Going to ``auth.<base>`` matches the cookie's
      // ``Domain=<base>`` scope and gets the operator the canonical
      // login UI without the path-prefix gymnastics.
      const here = window.location.pathname + window.location.search;
      const rd = encodeURIComponent(window.location.origin + here);
      window.setTimeout(() => {
        window.location.replace(`${authPortal()}/?rd=${rd}`);
      }, 1500);
    };

    // Listener 1 — existing 401-from-API path.
    const offAuth = onAuthEvent((event) => {
      if (event === "unauthenticated") redirectToLogin();
    });

    // Listener 2 — idle-tab liveness probe. Without it, an idle tab
    // past Authelia's `inactivity` window stays mounted in a stale
    // "you're signed in" state because no fetch fires to trigger
    // the 401-event path. Probe every 60s while foregrounded; pause
    // when hidden; immediate probe on mount + on tab-focus.
    let intervalId: number | undefined;
    const probe = async () => {
      if (redirected || isAuthPath) return;
      try {
        const res = await fetch("/api/me", {
          credentials: "include",
          headers: { Accept: "application/json" },
        });
        if (res.status === 401) redirectToLogin();
      } catch {
        // Transient network blip — don't redirect.
      }
    };
    const startPolling = () => {
      if (intervalId !== undefined) return;
      intervalId = window.setInterval(probe, 60_000);
    };
    const stopPolling = () => {
      if (intervalId !== undefined) {
        window.clearInterval(intervalId);
        intervalId = undefined;
      }
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        void probe();
        startPolling();
      } else {
        stopPolling();
      }
    };
    if (!isAuthPath && document.visibilityState === "visible") {
      void probe();
      startPolling();
    }
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      offAuth();
      stopPolling();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <TooltipPrimitive.Provider delayDuration={200} skipDelayDuration={300}>
          <ErrorBoundary>
            <RouterProvider router={router} />
          </ErrorBoundary>
          <Toaster
            position="bottom-right"
            theme="system"
            toastOptions={{
              className:
                "border border-border bg-bg-1 text-fg shadow-lg rounded-md",
            }}
          />
        </TooltipPrimitive.Provider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
