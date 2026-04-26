/// <reference types="vite/client" />
/// <reference types="vite-plugin-pwa/react" />

interface ImportMetaEnv {
  /** Build-time string baked from `package.json` `version` by
   *  `vite.config.ts`. Used by the SW cache-name (so a new build
   *  invalidates stale caches) and by the in-app drift banner that
   *  surfaces when the controller has moved past the SPA's build. */
  readonly VITE_BUILD_VERSION: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

declare module "virtual:pwa-register" {
  export interface RegisterSWOptions {
    immediate?: boolean;
    onNeedRefresh?: () => void;
    onOfflineReady?: () => void;
    onRegistered?: (
      registration: ServiceWorkerRegistration | undefined,
    ) => void;
    onRegisterError?: (error: unknown) => void;
  }
  export function registerSW(
    options?: RegisterSWOptions,
  ): (reload?: boolean) => Promise<void>;
}
