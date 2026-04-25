import { useEffect, useState } from "react";
import { registerSW } from "virtual:pwa-register";

interface PwaUpdate {
  hasUpdate: boolean;
  apply: () => void;
}

let _updateSW: ((reload?: boolean) => Promise<void>) | undefined;

export function initPwa(onUpdate: () => void): void {
  if (typeof window === "undefined") return;
  _updateSW = registerSW({
    onNeedRefresh: onUpdate,
    onOfflineReady() {
      // Telemetry hook only — no UI surface needed.
    },
  });
}

export function usePwaUpdate(): PwaUpdate {
  const [hasUpdate, setHasUpdate] = useState(false);
  useEffect(() => {
    initPwa(() => setHasUpdate(true));
  }, []);
  return {
    hasUpdate,
    apply: () => {
      void _updateSW?.(true);
    },
  };
}
