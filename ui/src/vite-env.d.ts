/// <reference types="vite/client" />
/// <reference types="vite-plugin-pwa/react" />

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
