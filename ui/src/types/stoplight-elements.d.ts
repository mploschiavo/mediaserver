/**
 * Local declaration shim for ``@stoplight/elements``.
 *
 * The upstream package ships ``index.d.ts`` but its ``package.json``
 * ``exports`` field lists only runtime-JS conditions (import / require)
 * without the matching ``types`` condition, so TypeScript's
 * ``bundler`` moduleResolution refuses to resolve the declaration.
 *
 * Re-declare the surface we actually use. Keep this in sync if a
 * future Stoplight upgrade changes ``APIProps``. (Track the upstream
 * issue: https://github.com/stoplightio/elements — they've been
 * asked to add ``types`` to exports several times.)
 */
declare module "@stoplight/elements" {
  import type { ComponentType } from "react";

  /** Subset of APIProps actually consumed by ``ui/src/routes/api-docs.tsx``.
   *  See node_modules/@stoplight/elements/containers/API.d.ts for the
   *  full upstream type. */
  export interface APIProps {
    apiDescriptionUrl?: string;
    apiDescriptionDocument?: string | object;
    router?: "history" | "hash" | "memory" | "static";
    basePath?: string;
    layout?: "sidebar" | "stacked" | "responsive";
    hideTryIt?: boolean;
    hideTryItPanel?: boolean;
    hideSchemas?: boolean;
    hideInternal?: boolean;
    hideExport?: boolean;
    tryItCredentialsPolicy?: "omit" | "include" | "same-origin";
    tryItCorsProxy?: string;
    logo?: string;
    staticRouterPath?: string;
  }

  export const API: ComponentType<APIProps>;
}

declare module "@stoplight/elements/styles.min.css";
