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

// ---- Routing v2 (PR-4 onward) ---------------------------------------------

/**
 * Subset of the v2 schema the UI reads. Mirrors the Python dataclasses
 * in `services/config/routing/schema_v2.py`. Treated as
 * `additionalProperties: true` because the backend may extend it
 * (Tier 1/2 fields land in subsequent PRs).
 */
export interface RoutingV2HostTls {
  cert_id?: string;
  force_https?: boolean;
}
export interface RoutingV2HostAuth {
  gate?: "required" | "optional" | "none";
  provider?: string;
}
export interface RoutingV2HostEntry {
  role: string;
  service_id: string;
  canonical: string;
  aliases?: string[];
  path_prefix?: string;
  tls?: RoutingV2HostTls;
  auth?: RoutingV2HostAuth;
  websocket?: boolean;
  timeout_seconds?: number;
  body_limit_mb?: number;
  maintenance?: boolean;
}
export interface RoutingV2Exposure {
  enabled: boolean;
  binding: "auto" | "k8s_ingress" | "k8s_loadbalancer" | "compose_host_port" | "compose_loopback";
  public_hostnames: string[];
  bind_addresses?: string[];
}
export interface RoutingV2PathAlias {
  from: string;
  to: string;
  code: number;
}
export interface RoutingV2Apex {
  action: "none" | "redirect" | "static" | "service";
  target?: string;
  code?: number;
}
export interface RoutingV2CatchAll {
  action: "404" | "redirect" | "block" | "service";
  target?: string;
  code?: number;
  custom_404_body?: string;
}
export interface RoutingV2CertManager {
  issuer_kind: "Issuer" | "ClusterIssuer";
  issuer_name: string;
  challenge: "http01" | "dns01";
  solver?: { provider: string; secret_ref?: string };
  secret_name?: string;
}
export interface RoutingV2Cert {
  id: string;
  source: "cert_manager" | "acme_direct" | "uploaded" | "cloudflare_origin";
  common_name: string;
  sans?: string[];
  cert_manager?: RoutingV2CertManager;
  expires_at?: string;
  auto_renew?: boolean;
  status?: "pending" | "ready" | "failed";
  failure_message?: string;
}
export interface RoutingV2Config {
  version: number;
  base_domain: string;
  stack_subdomain: string;
  gateway_host: string;
  gateway_port: number;
  strategy: "subdomain" | "path" | "hybrid";
  scheme: string;
  app_path_prefix: string;
  exposure: RoutingV2Exposure;
  hosts: RoutingV2HostEntry[];
  path_aliases: RoutingV2PathAlias[];
  apex: RoutingV2Apex;
  catch_all: RoutingV2CatchAll;
  certs: RoutingV2Cert[];
  defaults?: {
    websocket?: boolean;
    auth?: RoutingV2HostAuth;
    timeout_seconds?: number;
    body_limit_mb?: number;
  };
}
export interface RoutingV2ValidationError {
  code: string;
  field: string;
  message: string;
  hint?: string;
}
export interface RoutingV2Response {
  config: RoutingV2Config;
  validation: RoutingV2ValidationError[];
}

const ROUTING_V2_KEY = ["routing", "v2"] as const;

export function useRoutingV2(): UseQueryResult<RoutingV2Response> {
  return useQuery({
    queryKey: ROUTING_V2_KEY,
    queryFn: () => fetcher<RoutingV2Response>("api/routing/v2"),
    staleTime: 30_000,
  });
}

// ---- Routing config (POST /api/routing) -----------------------------------

export type RoutingStrategyValue = "hybrid" | "subdomain" | "path";

/**
 * Routing-config payload accepted by `POST /api/routing`. Only the
 * keys explicitly listed by the OpenAPI spec are sent. The controller
 * auto-syncs related fields (e.g. base_domain ↔ gateway_host).
 *
 * `direct_hosts` is a per-role hostname map. The backend merges
 * sub-keys into the existing config — set a sub-key to empty string
 * to clear that role without touching the others.
 */
export interface RoutingConfigInput {
  base_domain?: string;
  stack_subdomain?: string;
  gateway_host?: string;
  gateway_port?: number;
  app_path_prefix?: string;
  strategy?: RoutingStrategyValue;
  internet_exposed?: boolean;
  direct_hosts?: Record<string, string>;
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
