import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { HardDrive } from "lucide-react";
import { ApiErrorTile } from "@/components/ApiErrorTile";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useMe } from "@/features/me/hooks";
import { StorageActionButtons } from "./StorageActionButtons";
import { StorageCleanupPolicy } from "./StorageCleanupPolicy";
import { StorageStatusHeader } from "./StorageStatusHeader";
import { StorageThresholdInputs } from "./StorageThresholdInputs";
import { StorageTransitionFeed } from "./StorageTransitionFeed";
import { useDiskGuardrailsStatus } from "./hooks";
import { storageQueryKeys } from "./queryKeys";

/**
 * Subscribe to the unified `/api/events` stream for storage-domain
 * topics. When the controller publishes a `storage.lockdown_engaged`,
 * `storage.lockdown_released`, or `storage.cleanup_invoked` event,
 * this hook force-invalidates the status query so the card flips
 * tone without waiting on the 30-second poll.
 *
 * The controller does not yet publish these topics (Phase 4 will
 * teach `DownloadLockdownService.engage`/`release` to publish to
 * the EventBus). When the topics are absent, the hook is a no-op
 * — polling continues to drive freshness — so this is safe to
 * land before the publisher exists.
 *
 * Implementation note: the unified EventStreamProvider is mounted
 * once at the route root and wires its own `addEventListener`
 * loop. Rather than re-bind a second EventSource here, we hook
 * `window` for a synthetic `media-stack:event` CustomEvent that
 * the central handler can re-broadcast in a future tick. Today the
 * effect just stages the listener; once the central handler emits
 * the synthetic, the invalidation will start firing.
 */
function useStorageEventInvalidation(): void {
  const qc = useQueryClient();
  useEffect(() => {
    if (typeof window === "undefined") return;
    const handler = (ev: Event) => {
      const ce = ev as CustomEvent<{ event_type?: string }>;
      const eventType =
        typeof ce.detail?.event_type === "string"
          ? ce.detail.event_type
          : "";
      if (
        eventType.startsWith("storage.lockdown") ||
        eventType.startsWith("storage.cleanup")
      ) {
        void qc.invalidateQueries({ queryKey: storageQueryKeys.status });
      }
    };
    window.addEventListener(
      "media-stack:event",
      handler as EventListener,
    );
    return () => {
      window.removeEventListener(
        "media-stack:event",
        handler as EventListener,
      );
    };
  }, [qc]);
}

/**
 * Top-level Storage card — surfaced on the Ops page next to the
 * existing Stack health / Crashloops / Failed services tiles.
 *
 * Composition:
 *   - StorageStatusHeader     state badge, usage bar, paused chips
 *   - StorageActionButtons    cleanup / engage / release / pause / evaluate
 *   - StorageThresholdInputs  4 numeric inputs + Save
 *   - StorageCleanupPolicy    collapsed-by-default cleanup-policy section
 *   - StorageTransitionFeed   recent state-change feed from the audit log
 */
export function StorageCard() {
  const status = useDiskGuardrailsStatus();
  const me = useMe();
  useStorageEventInvalidation();

  const role = me.data?.role ?? "";
  const roleSlug = me.data?.role_slug ?? "";
  // Prefer the explicit ``controller_admin`` capability flag from
  // /api/me (set by the controller based on the role's
  // ``controller_admin`` field in ``contracts/roles.yaml``). The
  // role-slug allowlist is the fallback for backwards compatibility
  // with older controllers that don't surface the flag yet.
  const controllerAdmin = me.data?.controller_admin === true;
  const isAdmin =
    controllerAdmin ||
    role === "controller_admin" ||
    role === "admin" ||
    role === "superadmin" ||
    roleSlug === "controller_admin" ||
    roleSlug === "admin" ||
    roleSlug === "superadmin";
  const readOnly = !!me.data && !isAdmin;

  return (
    <Card data-testid="storage-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <HardDrive aria-hidden className="size-4 text-fg-muted" />
          Storage guardrails
        </CardTitle>
        <CardDescription>
          Disk-pressure status and manual controls. Lockdown pauses every
          download client; cleanup deletes oldest completed torrents.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {status.isLoading ? (
          <div
            className="flex flex-col gap-3"
            data-testid="storage-card-loading"
          >
            <Skeleton className="h-6 w-40" />
            <Skeleton className="h-2 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : status.error ? (
          <div data-testid="storage-card-error">
            <ApiErrorTile
              error={status.error}
              onRetry={() => void status.refetch()}
            />
          </div>
        ) : status.data ? (
          <>
            <StorageStatusHeader status={status.data} />
            <StorageActionButtons
              state={status.data.state}
              readOnly={readOnly}
            />
            <StorageThresholdInputs
              defaults={status.data.thresholds}
              readOnly={readOnly}
            />
            <StorageCleanupPolicy />
            <StorageTransitionFeed transitions={status.data.transitions} />
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}
