// Feature-local hooks for the routing-admin operator surface.
//
// The shared `src/api/hooks.ts` exports a `useRouting()` stub that
// returns the current strategy + per-app list. This module keeps that
// stub for the read side (see re-export below) and adds the wave-3
// additions: routing matrix probe, DNS-resolution probe, gateway
// hostname inventory, routing-config mutation, and the four TLS
// surfaces (describe / regenerate / install / download).
//
// Each new hook calls `fetcher` from `@/api/client` directly so the
// shared api/hooks barrel stays stable while concurrent agents
// (users-admin, content-admin, webhooks-snapshots) land sibling
// features without merge conflicts.
//
// All shapes here are deliberately permissive: the OpenAPI schema for
// the diagnostic endpoints is `additionalProperties: true`, so the UI
// types only document the fields it actually reads.

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { fetcher } from "@/api/client";

// Live `GET /api/routing` hook. The shared `src/api/hooks.ts`
// exports a stub of the same name returning a `{strategy, apps}`
// object that the controller never actually emits — we shadow it
// here with a real fetcher that returns the flat-config shape
// documented in the OpenAPI spec.
import type { paths } from "@/api/types";

export type RoutingResponse = NonNullable<
  paths["/api/routing"]["get"]["responses"]["200"]["content"]["application/json"]
>;

const ROUTING_KEY = ["routing"] as const;

export function useRouting(): UseQueryResult<RoutingResponse> {
  return useQuery({
    queryKey: ROUTING_KEY,
    queryFn: () => fetcher<RoutingResponse>("api/routing"),
    staleTime: 30_000,
  });
}

// ---- Routing config (POST /api/routing) -----------------------------------

export type RoutingStrategyValue = "hybrid" | "subdomain" | "path";

/**
 * Routing-config payload accepted by `POST /api/routing`. Only the
 * keys explicitly listed by the OpenAPI spec are sent. The controller
 * auto-syncs related fields (e.g. base_domain ↔ gateway_host).
 */
export interface RoutingConfigInput {
  base_domain?: string;
  stack_subdomain?: string;
  gateway_host?: string;
  gateway_port?: number;
  app_path_prefix?: string;
  strategy?: RoutingStrategyValue;
  internet_exposed?: boolean;
}

export interface RoutingUpdateResult {
  status: "updated" | "no_changes" | string;
  persisted_to?: string;
  changed?: readonly string[];
  routing?: Record<string, unknown>;
}

export function useUpdateRouting(): UseMutationResult<
  RoutingUpdateResult,
  Error,
  RoutingConfigInput
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body) =>
      fetcher<RoutingUpdateResult>("api/routing", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["routing"] });
      void qc.invalidateQueries({ queryKey: ["routing", "probe"] });
      void qc.invalidateQueries({ queryKey: ["routing", "gateway-hostnames"] });
    },
  });
}

// ---- Routing-matrix probe (GET /api/routing-probe) -----------------------

/**
 * One row of the matrix probe. The controller returns a permissive
 * map (additionalProperties: true); this type documents only the
 * fields the UI surfaces in the table.
 */
export interface RoutingProbeRow {
  app?: string;
  internal_url?: string;
  external_url?: string;
  ok?: boolean;
  status?: number;
  status_code?: number;
  latency_ms?: number;
  error?: string;
  probed_at?: string;
  [key: string]: unknown;
}

export interface RoutingProbeResult {
  rows?: readonly RoutingProbeRow[];
  results?: readonly RoutingProbeRow[];
  ts?: string;
  [key: string]: unknown;
}

const ROUTING_PROBE_KEY = ["routing", "probe"] as const;

export function useRoutingProbe(): UseQueryResult<RoutingProbeResult> {
  return useQuery({
    queryKey: ROUTING_PROBE_KEY,
    queryFn: () => fetcher<RoutingProbeResult>("api/routing-probe"),
    staleTime: 30_000,
  });
}

// ---- Single-route probe (GET /api/route-probe?url=...) -------------------

export interface SingleRouteProbeResult {
  ok?: boolean;
  status?: number;
  status_code?: number;
  latency_ms?: number;
  error?: string;
  url?: string;
  [key: string]: unknown;
}

export function useRouteProbe(): UseMutationResult<
  SingleRouteProbeResult,
  Error,
  string
> {
  return useMutation({
    mutationFn: (url) => {
      const params = new URLSearchParams({ url });
      return fetcher<SingleRouteProbeResult>(
        `api/route-probe?${params.toString()}`,
      );
    },
  });
}

// ---- DNS check (GET /api/dns-check) --------------------------------------

export interface DnsCheckEntry {
  hostname?: string;
  host?: string;
  resolved?: readonly string[];
  ips?: readonly string[];
  status?: "ok" | "missing" | "conflict" | string;
  error?: string;
  [key: string]: unknown;
}

export interface DnsCheckResult {
  entries?: readonly DnsCheckEntry[];
  results?: readonly DnsCheckEntry[];
  hostnames?: readonly DnsCheckEntry[];
  [key: string]: unknown;
}

const DNS_KEY = ["routing", "dns-check"] as const;

export function useDnsCheck(): UseQueryResult<DnsCheckResult> {
  return useQuery({
    queryKey: DNS_KEY,
    queryFn: () => fetcher<DnsCheckResult>("api/dns-check"),
    staleTime: 30_000,
  });
}

// ---- Gateway hostnames (GET /api/gateway-hostnames) ----------------------

export interface GatewayHostnamesResult {
  hostnames?: readonly string[];
}

const GATEWAY_KEY = ["routing", "gateway-hostnames"] as const;

export function useGatewayHostnames(): UseQueryResult<GatewayHostnamesResult> {
  return useQuery({
    queryKey: GATEWAY_KEY,
    queryFn: () => fetcher<GatewayHostnamesResult>("api/gateway-hostnames"),
    staleTime: 60_000,
  });
}

// ---- TLS certificate ------------------------------------------------------

/**
 * Edge-cert metadata returned by `GET /api/tls/certificate`. The
 * OpenAPI schema is `additionalProperties: true`; document the
 * fields the operator surface reads and accept anything else.
 */
export interface TlsCertificateInfo {
  subject?: string;
  subject_cn?: string;
  issuer?: string;
  san?: readonly string[];
  sans?: readonly string[];
  fingerprint?: string;
  fingerprint_sha256?: string;
  serial?: string;
  valid_from?: string;
  valid_to?: string;
  not_before?: string;
  not_after?: string;
  expires_at?: string;
  self_signed?: boolean;
  [key: string]: unknown;
}

const TLS_KEY = ["routing", "tls", "certificate"] as const;

export function useTlsCertificate(): UseQueryResult<TlsCertificateInfo> {
  return useQuery({
    queryKey: TLS_KEY,
    queryFn: () => fetcher<TlsCertificateInfo>("api/tls/certificate"),
    staleTime: 60_000,
  });
}

export function useRegenerateTlsCertificate(): UseMutationResult<
  unknown,
  Error,
  void
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      fetcher<unknown>("api/tls/certificate/regenerate", { method: "POST" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: TLS_KEY });
    },
  });
}

export interface InstallTlsCertificateInput {
  cert_pem: string;
  key_pem: string;
}

export function useInstallTlsCertificate(): UseMutationResult<
  unknown,
  Error,
  InstallTlsCertificateInput
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body) =>
      fetcher<unknown>("api/tls/certificate", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: TLS_KEY });
    },
  });
}

export const routingAdminQueryKeys = {
  routingProbe: ROUTING_PROBE_KEY,
  dnsCheck: DNS_KEY,
  gatewayHostnames: GATEWAY_KEY,
  tlsCertificate: TLS_KEY,
} as const;
