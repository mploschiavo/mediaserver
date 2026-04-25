import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createRouter } from "@tanstack/react-router";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { Toaster } from "sonner";
import { useEffect } from "react";
import { ErrorBoundary } from "@/components/layout/ErrorBoundary";
import { ThemeProvider } from "@/components/layout/ThemeProvider";
import { onAuthEvent } from "@/api/client";
import { routeTree } from "@/routeTree";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

const router = createRouter({ routeTree });

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
    return onAuthEvent((event) => {
      if (event !== "unauthenticated") return;
      if (redirected) return;
      const path = window.location.pathname;
      if (path.startsWith("/app/authelia") || path.startsWith("/api/verify")) {
        return;
      }
      redirected = true;
      window.location.replace("/app/authelia/");
    });
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
