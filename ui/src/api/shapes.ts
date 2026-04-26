// Hand-typed response shapes used by `endpoints.ts` until the OpenAPI
// codegen (`pnpm gen:api`) emits real `components["schemas"]` types.
// These mirror the Python service / handler returns:
//   - src/media_stack/services/media_integrity/service.py
//   - src/media_stack/api/services/media_integrity_handlers.py
// When the generated types land, swap each alias here for the
// generated `components["schemas"]["..."]` type and delete the rest.

export interface MediaIntegrityLastRun {
  ts: string;
  detail: Record<string, unknown>;
}

export interface MediaIntegrityStatusShape {
  last_enforce: MediaIntegrityLastRun;
  last_reconcile: MediaIntegrityLastRun;
  policy_version: number;
  servarr_adapters: readonly string[];
  bazarr_present: boolean;
  missing_api_keys: readonly string[];
}

export interface MediaIntegrityProgressIdle {
  in_progress: false;
}

export interface MediaIntegrityProgressActive {
  in_progress: true;
  op: "reconcile" | "enforce_config";
  started_at: string;
  phase: string;
  current: string | null;
  total: number | null;
  dry_run?: boolean;
}

export type MediaIntegrityProgressShape =
  | MediaIntegrityProgressIdle
  | MediaIntegrityProgressActive;

// The reconcile / enforce reports are large unions of per-adapter
// summaries. Until the codegen runs we expose them as opaque records;
// the UI never indexes into nested fields without first narrowing.
export interface ReconcileReportShape {
  dry_run: boolean;
  servarr: Record<string, unknown>;
  bazarr?: Record<string, unknown> | null;
  [key: string]: unknown;
}

export interface EnforceReportShape {
  servarr: Record<string, unknown>;
  bazarr?: Record<string, unknown> | null;
  [key: string]: unknown;
}

export interface ResolveReviewInput {
  app: string;
  release_id: string;
  winner_file_id?: string;
  winner_sub_path?: string;
  release_kind?: string;
  language?: string;
  forced?: boolean;
  hi?: boolean;
}

export interface ResolveReviewOutput {
  app: string;
  release_id: string;
  deleted_ids?: readonly string[];
  bytes_freed?: number;
  [key: string]: unknown;
}

export interface IdentityShape {
  authenticated: boolean;
  user?: string;
  display_name?: string;
  email?: string;
  groups?: string;
  // The dashboard uses `username`/`is_admin` shorthand; the controller
  // populates these for the admin-cookie path.
  username?: string;
  is_admin?: boolean;
}

export interface BrandingShape {
  brand?: {
    product_name?: string;
    logo_url?: string;
    [key: string]: unknown;
  };
  // Top-level alternates returned by some controller variants.
  product_name?: string;
  logo_url?: string;
}

export interface AuditEntry {
  ts?: string;
  actor?: string;
  action?: string;
  target?: string;
  result?: string;
  detail?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface AuditLogShape {
  entries: readonly AuditEntry[];
}

export interface SessionEntry {
  id?: string;
  user?: string;
  provider?: string;
  ip?: string;
  user_agent?: string;
  started_at?: string;
  last_seen_at?: string;
  [key: string]: unknown;
}

export interface SessionsShape {
  sessions: readonly SessionEntry[];
}

export interface HealthShape {
  status: string;
  [key: string]: unknown;
}

// ---- Dashboard skeleton tabs (Content/Logs/Ops/Routing/Webhooks/Users/Me) ----
// These shapes are best-effort hand-types so the placeholder pages can
// render real-shaped data while the controller surface catches up. As
// each endpoint lands on the backend, swap the alias for the generated
// `components["schemas"]["..."]` type.

export interface LibraryStatsShape {
  movies: number;
  tv: number;
  tracks: number;
  books: number;
}

export interface RecentItemShape {
  id: string;
  title: string;
  kind: "movie" | "tv" | "track" | "book";
  added_at: string;
}

export interface RecentAdditionsShape {
  items: readonly RecentItemShape[];
}

// Log source ids are dynamically derived from the controller's
// SERVICES registry via `GET /api/logs/sources` plus the platform
// pods (controller, ui). Previously this was a closed union of 8
// hardcoded values that didn't grow as new services were added —
// operators couldn't reach jellyfin/jellyseerr/sabnzbd/envoy/etc.
// logs even though they were running. Widened to `string` so the
// UI can render whatever the controller advertises at runtime.
export type LogSource = string;

export interface LogLineShape {
  ts: string;
  level: "info" | "warn" | "error" | "debug";
  message: string;
}

export interface LogStreamShape {
  source: LogSource;
  // The controller returns log lines as raw strings (one per
  // container/pod log row); the UI parses them into LogLineShape
  // before rendering. Older shape was a structured array — accept
  // both so a future controller change doesn't crash the page.
  lines: readonly (LogLineShape | string)[];
  // The backend returns 200 with `{lines:[], error:"..."}` when
  // the service look-up fails (label-selector mismatch, container
  // missing, etc.). The UI surfaces this string in the empty
  // state so operators see *why* there are no logs instead of a
  // silent blank panel.
  error?: string;
}

export interface RoutingStrategyShape {
  // Controller uses "path" (not "path-prefix") per the v1.3.2
  // OpenAPI tightening. Verified against the live `/api/routing`
  // payload — strategy enum is `subdomain | path | hybrid`.
  strategy: "subdomain" | "path" | "hybrid";
  base_domain: string;
  external_hostname: string;
  // Per-role hostname overrides keyed by role:
  //   media_server → jellyfin/plex/emby (resolved via technology_bindings)
  //   auth         → the configured auth provider (authelia/...)
  //   <other>      → tried as a literal service id
  direct_hosts?: Record<string, string>;
}

export interface RoutedAppShape {
  id: string;
  name: string;
  url: string;
  health: "healthy" | "degraded" | "down" | "unknown";
}

export interface RoutingShape {
  strategy: RoutingStrategyShape;
  apps: readonly RoutedAppShape[];
}

export interface WebhookEntryShape {
  id: string;
  url: string;
  events: readonly string[];
  last_fired_at?: string;
}

export interface WebhooksShape {
  webhooks: readonly WebhookEntryShape[];
}

export interface UserEntryShape {
  id: string;
  username: string;
  role: "admin" | "operator" | "viewer";
  last_login_at?: string;
  status: "active" | "disabled" | "pending";
}

export interface UsersShape {
  users: readonly UserEntryShape[];
  admins: number;
  pending_invites: number;
}

export interface OpsHealthShape {
  uptime_seconds: number;
  containers: number;
  disk_used_pct: number;
  last_bootstrap_at: string;
}

export interface MeSessionShape {
  id: string;
  device: string;
  ip: string;
  current?: boolean;
  last_seen_at: string;
}

export interface MeTokenShape {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at?: string;
}

export interface MeMfaShape {
  enabled: boolean;
  method?: "totp" | "webauthn";
}

export interface MeProfileShape {
  username: string;
  display_name: string;
  email: string;
  avatar_url?: string;
  sessions: readonly MeSessionShape[];
  tokens: readonly MeTokenShape[];
  mfa: MeMfaShape;
}
