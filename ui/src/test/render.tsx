import * as React from "react";
import { render, type RenderOptions } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TooltipProvider } from "@/components/ui/tooltip";

/**
 * Test-only render helper that wires the providers most components
 * need: a fresh React-Query client (with retry disabled so failed
 * queries don't slow tests) and Radix's TooltipProvider so any tree
 * containing a Tooltip can mount without warnings.
 *
 * Tests that need a router context should additionally wrap the
 * subject in a Tanstack memory router; doing it inline keeps the
 * router-aware tests obvious and lets non-router tests stay light.
 */
export function renderWithProviders(
  ui: React.ReactElement,
  options?: Omit<RenderOptions, "wrapper">,
) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={0} skipDelayDuration={0}>
        {children}
      </TooltipProvider>
    </QueryClientProvider>
  );
  return { queryClient, ...render(ui, { wrapper, ...options }) };
}

export { render } from "@testing-library/react";
