// Public surface of the API layer. Components import from "@/api".

export { ApiError, fetcher, getBaseUrl, onAuthEvent, setBaseUrl } from "./client";
export type { AuthEvent, FetcherInit } from "./client";
export { api } from "./endpoints";
export type { Api } from "./endpoints";
export {
  createQueryClient,
  queryClient,
  QueryProvider,
} from "./query-client";
export type { QueryProviderProps } from "./query-client";
export {
  queryKeys,
  useAuditLog,
  useBranding,
  useEnforceConfig,
  useHealth,
  useIdentity,
  useLogs,
  useMediaIntegrityProgress,
  useMediaIntegrityStatus,
  useOpsAction,
  useOpsHealth,
  useReconcile,
  useResolveReview,
  useRouting,
  useSessions,
  useUsers,
  useWebhooks,
} from "./hooks";
export type * from "./shapes";
