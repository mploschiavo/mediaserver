// AUTO-GENERATED — do not edit by hand.
// Source: contracts/api/openapi.yaml + tests/fixtures/api_responses
// Regenerate: python3 bin/ops/gen-fixture-codegen-validation.py
//
// What this exists for
// --------------------
// TypeScript-level contract that every captured live response (the
// fixtures committed under tests/fixtures/api_responses/) is
// structurally assignable to the spec-derived type from types.ts.
// tsc -b in `npm run build` / `npm run typecheck` validates these
// const assignments — drift produces a compile error.
//
// This catches the bug class where a UI card hand-rolls an
// interface that doesn't match the spec (e.g. {base_url} vs
// {url_template}). The card itself compiles fine; this file
// forces fixtures through the SPEC-DERIVED type so the divergence
// is caught.
//
// Skipped fixtures (no GET 200 schema, or x-status: planned):
//   apps.json — no GET 200 schema for /api/apps (or x-status: planned)
//   audit-log_head.json — no GET 200 schema for /api/audit-log/head (or x-status: planned)
//   audit_log_stats.json — no GET 200 schema for /api/audit/log/stats (or x-status: planned)
//   bans_ips.json — no GET 200 schema for /api/bans/ips (or x-status: planned)
//   bans_users.json — no GET 200 schema for /api/bans/users (or x-status: planned)
//   config.json — no GET 200 schema for /api/config (or x-status: planned)
//   disk_guardrails.json — no GET 200 schema for /api/disk/guardrails (or x-status: planned)
//   envoy_admin_summary.json — no GET 200 schema for /api/envoy/admin/summary (or x-status: planned)
//   healthz.json — no GET 200 schema for /api/healthz (or x-status: planned)
//   me_mfa-state.json — no GET 200 schema for /api/me/mfa-state (or x-status: planned)
//   me_sessions.json — no GET 200 schema for /api/me/sessions (or x-status: planned)
//   me_tokens.json — no GET 200 schema for /api/me/tokens (or x-status: planned)
//   media-integrity_progress.json — no GET 200 schema for /api/media-integrity/progress (or x-status: planned)
//   media-integrity_status.json — no GET 200 schema for /api/media-integrity/status (or x-status: planned)
//   readyz.json — no GET 200 schema for /api/readyz (or x-status: planned)
//   security_concurrent.json — no GET 200 schema for /api/security/concurrent (or x-status: planned)
//   security_failed-logins.json — no GET 200 schema for /api/security/failed-logins (or x-status: planned)
//   security_new-locations.json — no GET 200 schema for /api/security/new-locations (or x-status: planned)
//   sessions_active.json — no GET 200 schema for /api/sessions/active (or x-status: planned)
//   status.json — no GET 200 schema for /api/status (or x-status: planned)
//   sw_config.json — no GET 200 schema for /api/sw/config (or x-status: planned)
//   sw_config_json.json — no GET 200 schema for /api/sw/config/json (or x-status: planned)

/* eslint-disable @typescript-eslint/no-unused-vars */
/* eslint-disable unused-imports/no-unused-vars */

import type { paths } from "./types";

// Recursively widen string / number / boolean literals (and enum
// unions) to their base types. JSON imports lose literal-type
// information at the parse boundary, so asserting fixture:
// paths[...] would fail every enum field. Loosen distributes over
// unions (no [T] wrapper) so a property typed
// ``"a" | "b" | undefined`` becomes ``string | undefined``.
//
// What this preserves
// -------------------
// * Field names — extra/missing keys still error.
// * Object vs array — wrong-kind shape still errors.
// * Nested structure — every level recursed.
//
// What this loosens (intentional)
// -------------------------------
// * Enum values — the Python contract test uses jsonschema with
//   full enum validation. Don't double-up here.
// * String/number/boolean literal narrowing — JSON loses these.
type Loosen<T> =
    T extends readonly (infer U)[] ? Loosen<U>[] :
    T extends string ? string :
    T extends number ? number :
    T extends boolean ? boolean :
    T extends null ? null :
    T extends undefined ? undefined :
    // `Record<string, never>` is what openapi-typescript emits for
    // an unconstrained `type: object` (no properties / no
    // additionalProperties). The spec author meant "any object" —
    // widen to permit the live response.
    T extends Record<string, never> ? Record<string, unknown> :
    T extends object ? { [K in keyof T]: Loosen<T[K]> } :
    T;

// /api/access-urls
import fx_access_urls from "../../../tests/fixtures/api_responses/access-urls.json";
type T_fx_access_urls = paths["/api/access-urls"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_access_urls: Loosen<T_fx_access_urls> = fx_access_urls;
void _check_fx_access_urls;

// /api/arr-webhooks
import fx_arr_webhooks from "../../../tests/fixtures/api_responses/arr-webhooks.json";
type T_fx_arr_webhooks = paths["/api/arr-webhooks"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_arr_webhooks: Loosen<T_fx_arr_webhooks> = fx_arr_webhooks;
void _check_fx_arr_webhooks;

// /api/audit-log
import fx_audit_log from "../../../tests/fixtures/api_responses/audit-log.json";
type T_fx_audit_log = paths["/api/audit-log"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_audit_log: Loosen<T_fx_audit_log> = fx_audit_log;
void _check_fx_audit_log;

// /api/audit-log/verify
import fx_audit_log_verify from "../../../tests/fixtures/api_responses/audit-log_verify.json";
type T_fx_audit_log_verify = paths["/api/audit-log/verify"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_audit_log_verify: Loosen<T_fx_audit_log_verify> = fx_audit_log_verify;
void _check_fx_audit_log_verify;

// /api/auth/config
import fx_auth_config from "../../../tests/fixtures/api_responses/auth_config.json";
type T_fx_auth_config = paths["/api/auth/config"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_auth_config: Loosen<T_fx_auth_config> = fx_auth_config;
void _check_fx_auth_config;

// /api/auth/identity
import fx_auth_identity from "../../../tests/fixtures/api_responses/auth_identity.json";
type T_fx_auth_identity = paths["/api/auth/identity"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_auth_identity: Loosen<T_fx_auth_identity> = fx_auth_identity;
void _check_fx_auth_identity;

// /api/auth/modes
import fx_auth_modes from "../../../tests/fixtures/api_responses/auth_modes.json";
type T_fx_auth_modes = paths["/api/auth/modes"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_auth_modes: Loosen<T_fx_auth_modes> = fx_auth_modes;
void _check_fx_auth_modes;

// /api/auth/oidc-providers
import fx_auth_oidc_providers from "../../../tests/fixtures/api_responses/auth_oidc-providers.json";
type T_fx_auth_oidc_providers = paths["/api/auth/oidc-providers"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_auth_oidc_providers: Loosen<T_fx_auth_oidc_providers> = fx_auth_oidc_providers;
void _check_fx_auth_oidc_providers;

// /api/auth/service-policies
import fx_auth_service_policies from "../../../tests/fixtures/api_responses/auth_service-policies.json";
type T_fx_auth_service_policies = paths["/api/auth/service-policies"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_auth_service_policies: Loosen<T_fx_auth_service_policies> = fx_auth_service_policies;
void _check_fx_auth_service_policies;

// /api/auto-heal
import fx_auto_heal from "../../../tests/fixtures/api_responses/auto-heal.json";
type T_fx_auto_heal = paths["/api/auto-heal"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_auto_heal: Loosen<T_fx_auto_heal> = fx_auto_heal;
void _check_fx_auto_heal;

// /api/backup
import fx_backup from "../../../tests/fixtures/api_responses/backup.json";
type T_fx_backup = paths["/api/backup"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_backup: Loosen<T_fx_backup> = fx_backup;
void _check_fx_backup;

// /api/branding
import fx_branding from "../../../tests/fixtures/api_responses/branding.json";
type T_fx_branding = paths["/api/branding"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_branding: Loosen<T_fx_branding> = fx_branding;
void _check_fx_branding;

// /api/cleanup-preview
import fx_cleanup_preview from "../../../tests/fixtures/api_responses/cleanup-preview.json";
type T_fx_cleanup_preview = paths["/api/cleanup-preview"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_cleanup_preview: Loosen<T_fx_cleanup_preview> = fx_cleanup_preview;
void _check_fx_cleanup_preview;

// /api/config-drift
import fx_config_drift from "../../../tests/fixtures/api_responses/config-drift.json";
type T_fx_config_drift = paths["/api/config-drift"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_config_drift: Loosen<T_fx_config_drift> = fx_config_drift;
void _check_fx_config_drift;

// /api/config/libraries
import fx_config_libraries from "../../../tests/fixtures/api_responses/config_libraries.json";
type T_fx_config_libraries = paths["/api/config/libraries"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_config_libraries: Loosen<T_fx_config_libraries> = fx_config_libraries;
void _check_fx_config_libraries;

// /api/credentials
import fx_credentials from "../../../tests/fixtures/api_responses/credentials.json";
type T_fx_credentials = paths["/api/credentials"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_credentials: Loosen<T_fx_credentials> = fx_credentials;
void _check_fx_credentials;

// /api/discovery-lists
import fx_discovery_lists from "../../../tests/fixtures/api_responses/discovery-lists.json";
type T_fx_discovery_lists = paths["/api/discovery-lists"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_discovery_lists: Loosen<T_fx_discovery_lists> = fx_discovery_lists;
void _check_fx_discovery_lists;

// /api/discovery/popular-tv
import fx_discovery_popular_tv from "../../../tests/fixtures/api_responses/discovery_popular-tv.json";
type T_fx_discovery_popular_tv = paths["/api/discovery/popular-tv"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_discovery_popular_tv: Loosen<T_fx_discovery_popular_tv> = fx_discovery_popular_tv;
void _check_fx_discovery_popular_tv;

// /api/disk
import fx_disk from "../../../tests/fixtures/api_responses/disk.json";
type T_fx_disk = paths["/api/disk"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_disk: Loosen<T_fx_disk> = fx_disk;
void _check_fx_disk;

// /api/display-preferences
import fx_display_preferences from "../../../tests/fixtures/api_responses/display-preferences.json";
type T_fx_display_preferences = paths["/api/display-preferences"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_display_preferences: Loosen<T_fx_display_preferences> = fx_display_preferences;
void _check_fx_display_preferences;

// /api/dns-check
import fx_dns_check from "../../../tests/fixtures/api_responses/dns-check.json";
type T_fx_dns_check = paths["/api/dns-check"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_dns_check: Loosen<T_fx_dns_check> = fx_dns_check;
void _check_fx_dns_check;

// /api/download-analytics
import fx_download_analytics from "../../../tests/fixtures/api_responses/download-analytics.json";
type T_fx_download_analytics = paths["/api/download-analytics"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_download_analytics: Loosen<T_fx_download_analytics> = fx_download_analytics;
void _check_fx_download_analytics;

// /api/download-categories
import fx_download_categories from "../../../tests/fixtures/api_responses/download-categories.json";
type T_fx_download_categories = paths["/api/download-categories"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_download_categories: Loosen<T_fx_download_categories> = fx_download_categories;
void _check_fx_download_categories;

// /api/download-client-settings
import fx_download_client_settings from "../../../tests/fixtures/api_responses/download-client-settings.json";
type T_fx_download_client_settings = paths["/api/download-client-settings"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_download_client_settings: Loosen<T_fx_download_client_settings> = fx_download_client_settings;
void _check_fx_download_client_settings;

// /api/download-history
import fx_download_history from "../../../tests/fixtures/api_responses/download-history.json";
type T_fx_download_history = paths["/api/download-history"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_download_history: Loosen<T_fx_download_history> = fx_download_history;
void _check_fx_download_history;

// /api/downloads
import fx_downloads from "../../../tests/fixtures/api_responses/downloads.json";
type T_fx_downloads = paths["/api/downloads"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_downloads: Loosen<T_fx_downloads> = fx_downloads;
void _check_fx_downloads;

// /api/env
import fx_env from "../../../tests/fixtures/api_responses/env.json";
type T_fx_env = paths["/api/env"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_env: Loosen<T_fx_env> = fx_env;
void _check_fx_env;

// /api/envoy/stats
import fx_envoy_stats from "../../../tests/fixtures/api_responses/envoy_stats.json";
type T_fx_envoy_stats = paths["/api/envoy/stats"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_envoy_stats: Loosen<T_fx_envoy_stats> = fx_envoy_stats;
void _check_fx_envoy_stats;

// /api/envvars
import fx_envvars from "../../../tests/fixtures/api_responses/envvars.json";
type T_fx_envvars = paths["/api/envvars"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_envvars: Loosen<T_fx_envvars> = fx_envvars;
void _check_fx_envvars;

// /api/epg-health
import fx_epg_health from "../../../tests/fixtures/api_responses/epg-health.json";
type T_fx_epg_health = paths["/api/epg-health"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_epg_health: Loosen<T_fx_epg_health> = fx_epg_health;
void _check_fx_epg_health;

// /api/epg-providers
import fx_epg_providers from "../../../tests/fixtures/api_responses/epg-providers.json";
type T_fx_epg_providers = paths["/api/epg-providers"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_epg_providers: Loosen<T_fx_epg_providers> = fx_epg_providers;
void _check_fx_epg_providers;

// /api/failed-services
import fx_failed_services from "../../../tests/fixtures/api_responses/failed-services.json";
type T_fx_failed_services = paths["/api/failed-services"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_failed_services: Loosen<T_fx_failed_services> = fx_failed_services;
void _check_fx_failed_services;

// /api/gateway-hostnames
import fx_gateway_hostnames from "../../../tests/fixtures/api_responses/gateway-hostnames.json";
type T_fx_gateway_hostnames = paths["/api/gateway-hostnames"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_gateway_hostnames: Loosen<T_fx_gateway_hostnames> = fx_gateway_hostnames;
void _check_fx_gateway_hostnames;

// /api/gpu
import fx_gpu from "../../../tests/fixtures/api_responses/gpu.json";
type T_fx_gpu = paths["/api/gpu"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_gpu: Loosen<T_fx_gpu> = fx_gpu;
void _check_fx_gpu;

// /api/grafana.json
import fx_grafana_json from "../../../tests/fixtures/api_responses/grafana.json.json";
type T_fx_grafana_json = paths["/api/grafana.json"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_grafana_json: Loosen<T_fx_grafana_json> = fx_grafana_json;
void _check_fx_grafana_json;

// /api/guardrails
import fx_guardrails from "../../../tests/fixtures/api_responses/guardrails.json";
type T_fx_guardrails = paths["/api/guardrails"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_guardrails: Loosen<T_fx_guardrails> = fx_guardrails;
void _check_fx_guardrails;

// /api/health-history
import fx_health_history from "../../../tests/fixtures/api_responses/health-history.json";
type T_fx_health_history = paths["/api/health-history"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_health_history: Loosen<T_fx_health_history> = fx_health_history;
void _check_fx_health_history;

// /api/health
import fx_health from "../../../tests/fixtures/api_responses/health.json";
type T_fx_health = paths["/api/health"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_health: Loosen<T_fx_health> = fx_health;
void _check_fx_health;

// /api/health/config-integrity
import fx_health_config_integrity from "../../../tests/fixtures/api_responses/health_config-integrity.json";
type T_fx_health_config_integrity = paths["/api/health/config-integrity"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_health_config_integrity: Loosen<T_fx_health_config_integrity> = fx_health_config_integrity;
void _check_fx_health_config_integrity;

// /api/health/crashloops
import fx_health_crashloops from "../../../tests/fixtures/api_responses/health_crashloops.json";
type T_fx_health_crashloops = paths["/api/health/crashloops"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_health_crashloops: Loosen<T_fx_health_crashloops> = fx_health_crashloops;
void _check_fx_health_crashloops;

// /api/health/stories
import fx_health_stories from "../../../tests/fixtures/api_responses/health_stories.json";
type T_fx_health_stories = paths["/api/health/stories"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_health_stories: Loosen<T_fx_health_stories> = fx_health_stories;
void _check_fx_health_stories;

// /api/image-updates
import fx_image_updates from "../../../tests/fixtures/api_responses/image-updates.json";
type T_fx_image_updates = paths["/api/image-updates"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_image_updates: Loosen<T_fx_image_updates> = fx_image_updates;
void _check_fx_image_updates;

// /api/import-lists-all
import fx_import_lists_all from "../../../tests/fixtures/api_responses/import-lists-all.json";
type T_fx_import_lists_all = paths["/api/import-lists-all"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_import_lists_all: Loosen<T_fx_import_lists_all> = fx_import_lists_all;
void _check_fx_import_lists_all;

// /api/import-lists
import fx_import_lists from "../../../tests/fixtures/api_responses/import-lists.json";
type T_fx_import_lists = paths["/api/import-lists"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_import_lists: Loosen<T_fx_import_lists> = fx_import_lists;
void _check_fx_import_lists;

// /api/indexer-stats
import fx_indexer_stats from "../../../tests/fixtures/api_responses/indexer-stats.json";
type T_fx_indexer_stats = paths["/api/indexer-stats"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_indexer_stats: Loosen<T_fx_indexer_stats> = fx_indexer_stats;
void _check_fx_indexer_stats;

// /api/indexers
import fx_indexers from "../../../tests/fixtures/api_responses/indexers.json";
type T_fx_indexers = paths["/api/indexers"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_indexers: Loosen<T_fx_indexers> = fx_indexers;
void _check_fx_indexers;

// /api/invites
import fx_invites from "../../../tests/fixtures/api_responses/invites.json";
type T_fx_invites = paths["/api/invites"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_invites: Loosen<T_fx_invites> = fx_invites;
void _check_fx_invites;

// /api/iptv-countries
import fx_iptv_countries from "../../../tests/fixtures/api_responses/iptv-countries.json";
type T_fx_iptv_countries = paths["/api/iptv-countries"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_iptv_countries: Loosen<T_fx_iptv_countries> = fx_iptv_countries;
void _check_fx_iptv_countries;

// /api/jobs
import fx_jobs from "../../../tests/fixtures/api_responses/jobs.json";
type T_fx_jobs = paths["/api/jobs"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_jobs: Loosen<T_fx_jobs> = fx_jobs;
void _check_fx_jobs;

// /api/jobs/queue
import fx_jobs_queue from "../../../tests/fixtures/api_responses/jobs_queue.json";
type T_fx_jobs_queue = paths["/api/jobs/queue"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_jobs_queue: Loosen<T_fx_jobs_queue> = fx_jobs_queue;
void _check_fx_jobs_queue;

// /api/jobs/running
import fx_jobs_running from "../../../tests/fixtures/api_responses/jobs_running.json";
type T_fx_jobs_running = paths["/api/jobs/running"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_jobs_running: Loosen<T_fx_jobs_running> = fx_jobs_running;
void _check_fx_jobs_running;

// /api/keys
import fx_keys from "../../../tests/fixtures/api_responses/keys.json";
type T_fx_keys = paths["/api/keys"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_keys: Loosen<T_fx_keys> = fx_keys;
void _check_fx_keys;

// /api/libraries
import fx_libraries from "../../../tests/fixtures/api_responses/libraries.json";
type T_fx_libraries = paths["/api/libraries"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_libraries: Loosen<T_fx_libraries> = fx_libraries;
void _check_fx_libraries;

// /api/livetv-sources
import fx_livetv_sources from "../../../tests/fixtures/api_responses/livetv-sources.json";
type T_fx_livetv_sources = paths["/api/livetv-sources"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_livetv_sources: Loosen<T_fx_livetv_sources> = fx_livetv_sources;
void _check_fx_livetv_sources;

// /api/log-level
import fx_log_level from "../../../tests/fixtures/api_responses/log-level.json";
type T_fx_log_level = paths["/api/log-level"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_log_level: Loosen<T_fx_log_level> = fx_log_level;
void _check_fx_log_level;

// /api/logs
import fx_logs from "../../../tests/fixtures/api_responses/logs.json";
type T_fx_logs = paths["/api/logs"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_logs: Loosen<T_fx_logs> = fx_logs;
void _check_fx_logs;

// /api/logs/sources
import fx_logs_sources from "../../../tests/fixtures/api_responses/logs_sources.json";
type T_fx_logs_sources = paths["/api/logs/sources"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_logs_sources: Loosen<T_fx_logs_sources> = fx_logs_sources;
void _check_fx_logs_sources;

// /api/manifests
import fx_manifests from "../../../tests/fixtures/api_responses/manifests.json";
type T_fx_manifests = paths["/api/manifests"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_manifests: Loosen<T_fx_manifests> = fx_manifests;
void _check_fx_manifests;

// /api/me
import fx_me from "../../../tests/fixtures/api_responses/me.json";
type T_fx_me = paths["/api/me"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_me: Loosen<T_fx_me> = fx_me;
void _check_fx_me;

// /api/metadata-settings
import fx_metadata_settings from "../../../tests/fixtures/api_responses/metadata-settings.json";
type T_fx_metadata_settings = paths["/api/metadata-settings"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_metadata_settings: Loosen<T_fx_metadata_settings> = fx_metadata_settings;
void _check_fx_metadata_settings;

// /api/mounts
import fx_mounts from "../../../tests/fixtures/api_responses/mounts.json";
type T_fx_mounts = paths["/api/mounts"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_mounts: Loosen<T_fx_mounts> = fx_mounts;
void _check_fx_mounts;

// /api/namespaces
import fx_namespaces from "../../../tests/fixtures/api_responses/namespaces.json";
type T_fx_namespaces = paths["/api/namespaces"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_namespaces: Loosen<T_fx_namespaces> = fx_namespaces;
void _check_fx_namespaces;

// /api/onboarding
import fx_onboarding from "../../../tests/fixtures/api_responses/onboarding.json";
type T_fx_onboarding = paths["/api/onboarding"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_onboarding: Loosen<T_fx_onboarding> = fx_onboarding;
void _check_fx_onboarding;

// /api/openapi.json
import fx_openapi_json from "../../../tests/fixtures/api_responses/openapi.json.json";
type T_fx_openapi_json = paths["/api/openapi.json"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_openapi_json: Loosen<T_fx_openapi_json> = fx_openapi_json;
void _check_fx_openapi_json;

// /api/ops/health
import fx_ops_health from "../../../tests/fixtures/api_responses/ops_health.json";
type T_fx_ops_health = paths["/api/ops/health"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_ops_health: Loosen<T_fx_ops_health> = fx_ops_health;
void _check_fx_ops_health;

// /api/orchestrator/promises/state
import fx_orchestrator_promises_state from "../../../tests/fixtures/api_responses/orchestrator_promises_state.json";
type T_fx_orchestrator_promises_state = paths["/api/orchestrator/promises/state"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_orchestrator_promises_state: Loosen<T_fx_orchestrator_promises_state> = fx_orchestrator_promises_state;
void _check_fx_orchestrator_promises_state;

// /api/password-policy
import fx_password_policy from "../../../tests/fixtures/api_responses/password-policy.json";
type T_fx_password_policy = paths["/api/password-policy"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_password_policy: Loosen<T_fx_password_policy> = fx_password_policy;
void _check_fx_password_policy;

// /api/password-propagation
import fx_password_propagation from "../../../tests/fixtures/api_responses/password-propagation.json";
type T_fx_password_propagation = paths["/api/password-propagation"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_password_propagation: Loosen<T_fx_password_propagation> = fx_password_propagation;
void _check_fx_password_propagation;

// /api/profile
import fx_profile from "../../../tests/fixtures/api_responses/profile.json";
type T_fx_profile = paths["/api/profile"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_profile: Loosen<T_fx_profile> = fx_profile;
void _check_fx_profile;

// /api/quality-presets
import fx_quality_presets from "../../../tests/fixtures/api_responses/quality-presets.json";
type T_fx_quality_presets = paths["/api/quality-presets"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_quality_presets: Loosen<T_fx_quality_presets> = fx_quality_presets;
void _check_fx_quality_presets;

// /api/quality-profiles
import fx_quality_profiles from "../../../tests/fixtures/api_responses/quality-profiles.json";
type T_fx_quality_profiles = paths["/api/quality-profiles"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_quality_profiles: Loosen<T_fx_quality_profiles> = fx_quality_profiles;
void _check_fx_quality_profiles;

// /api/recent
import fx_recent from "../../../tests/fixtures/api_responses/recent.json";
type T_fx_recent = paths["/api/recent"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_recent: Loosen<T_fx_recent> = fx_recent;
void _check_fx_recent;

// /api/roles
import fx_roles from "../../../tests/fixtures/api_responses/roles.json";
type T_fx_roles = paths["/api/roles"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_roles: Loosen<T_fx_roles> = fx_roles;
void _check_fx_roles;

// /api/route-probe
import fx_route_probe from "../../../tests/fixtures/api_responses/route-probe.json";
type T_fx_route_probe = paths["/api/route-probe"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_route_probe: Loosen<T_fx_route_probe> = fx_route_probe;
void _check_fx_route_probe;

// /api/routing-probe
import fx_routing_probe from "../../../tests/fixtures/api_responses/routing-probe.json";
type T_fx_routing_probe = paths["/api/routing-probe"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_routing_probe: Loosen<T_fx_routing_probe> = fx_routing_probe;
void _check_fx_routing_probe;

// /api/routing
import fx_routing from "../../../tests/fixtures/api_responses/routing.json";
type T_fx_routing = paths["/api/routing"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_routing: Loosen<T_fx_routing> = fx_routing;
void _check_fx_routing;

// /api/routing/effective
import fx_routing_effective from "../../../tests/fixtures/api_responses/routing_effective.json";
type T_fx_routing_effective = paths["/api/routing/effective"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_routing_effective: Loosen<T_fx_routing_effective> = fx_routing_effective;
void _check_fx_routing_effective;

// /api/routing/preview
import fx_routing_preview from "../../../tests/fixtures/api_responses/routing_preview.json";
type T_fx_routing_preview = paths["/api/routing/preview"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_routing_preview: Loosen<T_fx_routing_preview> = fx_routing_preview;
void _check_fx_routing_preview;

// /api/routing/routes
import fx_routing_routes from "../../../tests/fixtures/api_responses/routing_routes.json";
type T_fx_routing_routes = paths["/api/routing/routes"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_routing_routes: Loosen<T_fx_routing_routes> = fx_routing_routes;
void _check_fx_routing_routes;

// /api/routing/v2
import fx_routing_v2 from "../../../tests/fixtures/api_responses/routing_v2.json";
type T_fx_routing_v2 = paths["/api/routing/v2"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_routing_v2: Loosen<T_fx_routing_v2> = fx_routing_v2;
void _check_fx_routing_v2;

// /api/runs
import fx_runs from "../../../tests/fixtures/api_responses/runs.json";
type T_fx_runs = paths["/api/runs"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_runs: Loosen<T_fx_runs> = fx_runs;
void _check_fx_runs;

// /api/schedules
import fx_schedules from "../../../tests/fixtures/api_responses/schedules.json";
type T_fx_schedules = paths["/api/schedules"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_schedules: Loosen<T_fx_schedules> = fx_schedules;
void _check_fx_schedules;

// /api/services
import fx_services from "../../../tests/fixtures/api_responses/services.json";
type T_fx_services = paths["/api/services"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_services: Loosen<T_fx_services> = fx_services;
void _check_fx_services;

// /api/services/categories
import fx_services_categories from "../../../tests/fixtures/api_responses/services_categories.json";
type T_fx_services_categories = paths["/api/services/categories"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_services_categories: Loosen<T_fx_services_categories> = fx_services_categories;
void _check_fx_services_categories;

// /api/snapshot-diff
import fx_snapshot_diff from "../../../tests/fixtures/api_responses/snapshot-diff.json";
type T_fx_snapshot_diff = paths["/api/snapshot-diff"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_snapshot_diff: Loosen<T_fx_snapshot_diff> = fx_snapshot_diff;
void _check_fx_snapshot_diff;

// /api/snapshots
import fx_snapshots from "../../../tests/fixtures/api_responses/snapshots.json";
type T_fx_snapshots = paths["/api/snapshots"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_snapshots: Loosen<T_fx_snapshots> = fx_snapshots;
void _check_fx_snapshots;

// /api/stack/update
import fx_stack_update from "../../../tests/fixtures/api_responses/stack_update.json";
type T_fx_stack_update = paths["/api/stack/update"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_stack_update: Loosen<T_fx_stack_update> = fx_stack_update;
void _check_fx_stack_update;

// /api/stats
import fx_stats from "../../../tests/fixtures/api_responses/stats.json";
type T_fx_stats = paths["/api/stats"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_stats: Loosen<T_fx_stats> = fx_stats;
void _check_fx_stats;

// /api/storage-breakdown
import fx_storage_breakdown from "../../../tests/fixtures/api_responses/storage-breakdown.json";
type T_fx_storage_breakdown = paths["/api/storage-breakdown"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_storage_breakdown: Loosen<T_fx_storage_breakdown> = fx_storage_breakdown;
void _check_fx_storage_breakdown;

// /api/telemetry
import fx_telemetry from "../../../tests/fixtures/api_responses/telemetry.json";
type T_fx_telemetry = paths["/api/telemetry"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_telemetry: Loosen<T_fx_telemetry> = fx_telemetry;
void _check_fx_telemetry;

// /api/tls/certificate
import fx_tls_certificate from "../../../tests/fixtures/api_responses/tls_certificate.json";
type T_fx_tls_certificate = paths["/api/tls/certificate"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_tls_certificate: Loosen<T_fx_tls_certificate> = fx_tls_certificate;
void _check_fx_tls_certificate;

// /api/tokens
import fx_tokens from "../../../tests/fixtures/api_responses/tokens.json";
type T_fx_tokens = paths["/api/tokens"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_tokens: Loosen<T_fx_tokens> = fx_tokens;
void _check_fx_tokens;

// /api/user-providers
import fx_user_providers from "../../../tests/fixtures/api_responses/user-providers.json";
type T_fx_user_providers = paths["/api/user-providers"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_user_providers: Loosen<T_fx_user_providers> = fx_user_providers;
void _check_fx_user_providers;

// /api/users-reconcile
import fx_users_reconcile from "../../../tests/fixtures/api_responses/users-reconcile.json";
type T_fx_users_reconcile = paths["/api/users-reconcile"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_users_reconcile: Loosen<T_fx_users_reconcile> = fx_users_reconcile;
void _check_fx_users_reconcile;

// /api/users
import fx_users from "../../../tests/fixtures/api_responses/users.json";
type T_fx_users = paths["/api/users"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_users: Loosen<T_fx_users> = fx_users;
void _check_fx_users;

// /api/versions
import fx_versions from "../../../tests/fixtures/api_responses/versions.json";
type T_fx_versions = paths["/api/versions"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_versions: Loosen<T_fx_versions> = fx_versions;
void _check_fx_versions;

// /webhooks
import fx_webhooks from "../../../tests/fixtures/api_responses/webhooks.json";
type T_fx_webhooks = paths["/webhooks"]["get"]["responses"][200]["content"]["application/json"];
const _check_fx_webhooks: Loosen<T_fx_webhooks> = fx_webhooks;
void _check_fx_webhooks;
