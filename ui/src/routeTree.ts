/**
 * Hand-rolled route tree. We compose Tanstack Router routes by
 * hand here rather than letting `@tanstack/router-plugin` codegen
 * the tree, because the placeholder phase only has a handful of
 * routes and a deterministic, reviewable file is preferable to a
 * generated artifact during the migration window.
 */
import { Route as RootRoute } from "@/routes/__root";
import { Route as IndexRoute } from "@/routes/index";
import { Route as NotFoundRoute } from "@/routes/$";
import { Route as BansRoute } from "@/routes/bans";
import { SessionsRoute } from "@/routes/sessions";
import { Route as AuditLogRoute } from "@/routes/audit-log";
import { Route as AuthAdminRoute } from "@/routes/auth";
import { Route as GuardrailsRoute } from "@/routes/guardrails";
import { Route as JobsRoute } from "@/routes/jobs";
import { Route as LivetvRoute } from "@/routes/livetv";
import { Route as SecurityRoute } from "@/routes/security";
import { Route as SnapshotsRoute } from "@/routes/snapshots";
import { Route as ApiDocsRoute } from "@/routes/api-docs";
import {
  ContentRoute,
  LogsRoute,
  OpsRoute,
  ProfileRoute,
  RoutingRoute,
  WebhooksRoute,
  UsersRoute,
  MediaIntegrityRoute,
  MeRoute,
  SettingsRoute,
} from "@/routes/$placeholder";

export const routeTree = RootRoute.addChildren([
  IndexRoute,
  ContentRoute,
  LogsRoute,
  OpsRoute,
  ProfileRoute,
  RoutingRoute,
  WebhooksRoute,
  UsersRoute,
  MediaIntegrityRoute,
  MeRoute,
  SettingsRoute,
  BansRoute,
  SessionsRoute,
  AuditLogRoute,
  AuthAdminRoute,
  GuardrailsRoute,
  JobsRoute,
  LivetvRoute,
  SecurityRoute,
  SnapshotsRoute,
  ApiDocsRoute,
  // Splat catch-all is registered last so explicit paths win the match.
  NotFoundRoute,
]);
