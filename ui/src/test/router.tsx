import * as React from "react";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderOptions } from "@testing-library/react";
import { TooltipProvider } from "@/components/ui/tooltip";

interface RouterRenderOptions extends Omit<RenderOptions, "wrapper"> {
  /** Initial location pathname for the memory router. Defaults to "/". */
  initialPath?: string;
  /** Routes to register beyond "/" + a catch-all. */
  paths?: string[];
}

/**
 * Mount a component inside a Tanstack memory router. The component
 * under test renders at the leaf of every registered path so tests
 * that flip `initialPath` simply rerender the same subject in a
 * different location context.
 */
export function renderWithRouter(
  ui: React.ReactElement,
  { initialPath = "/", paths = [], ...options }: RouterRenderOptions = {},
) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });

  const rootRoute = createRootRoute({
    component: () => (
      <>
        {ui}
        <Outlet />
      </>
    ),
  });

  const childRoutes = ["/", ...paths].map((path) =>
    createRoute({
      getParentRoute: () => rootRoute,
      path,
      component: () => null,
    }),
  );
  rootRoute.addChildren(childRoutes);

  const router = createRouter({
    routeTree: rootRoute,
    history: createMemoryHistory({ initialEntries: [initialPath] }),
  });

  const wrapper = ({ children: _children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={0} skipDelayDuration={0}>
        <RouterProvider router={router} />
      </TooltipProvider>
    </QueryClientProvider>
  );

  return {
    router,
    queryClient,
    // We render an empty placeholder; the wrapper paints the UI via
    // RouterProvider so the router context propagates correctly.
    ...render(<></>, { wrapper, ...options }),
  };
}
