// Tanstack Query plumbing for the dashboard.
//
// Defaults are tuned for an admin-console workload: most endpoints
// return small JSON, polling is opt-in per query, and we never retry
// 4xx because they signal a programming/auth error, not transient
// failure.

import {
  QueryClient,
  QueryClientProvider as TanstackQueryClientProvider,
} from "@tanstack/react-query";
import { createElement, type ReactNode } from "react";

import { ApiError } from "./client";

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        gcTime: 300_000,
        refetchOnWindowFocus: true,
        retry: (failureCount, error) => {
          if (failureCount >= 2) return false;
          if (error instanceof ApiError) return error.status >= 500;
          return true;
        },
      },
      mutations: {
        // Mutations are user-initiated; surface failures immediately
        // rather than silently retrying a possibly destructive call.
        retry: false,
      },
    },
  });
}

export const queryClient = createQueryClient();

export interface QueryProviderProps {
  client?: QueryClient;
  children: ReactNode;
}

export function QueryProvider({
  client,
  children,
}: QueryProviderProps): ReactNode {
  return createElement(
    TanstackQueryClientProvider,
    { client: client ?? queryClient },
    children,
  );
}
