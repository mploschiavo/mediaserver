import { useCallback, useId, useState } from "react";
import { ArrowUpCircle } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useStackUpdate, useStackUpgrade } from "./hooks";
import { UpgradeProgressDialog } from "./UpgradeProgressDialog";

// The exact phrase the operator must type before the Confirm
// button unlocks. This is a contract — it is compared with `===`
// against the input value, no trim, no toLowerCase, no regex.
// Mirrors `EmergencyRevokeCard`'s "REVOKE ALL" gate; changing this
// string is a UX-breaking change.
const CONFIRM_PHRASE = "UPGRADE";

/**
 * Top-of-page banner shown when the controller reports a newer
 * release is available (`GET /api/stack/update` → `available: true`).
 * Renders nothing on error so a flaky probe never breaks the shell.
 *
 * "Upgrade now" opens a Dialog with a release-notes preview and a
 * two-step "Type UPGRADE to confirm" gate. On confirm the mutation
 * fires and the banner mounts the progress dialog with the returned
 * task_id.
 */
export function UpgradeBanner() {
  const update = useStackUpdate();
  const upgrade = useStackUpgrade();
  const [open, setOpen] = useState(false);
  const [confirmText, setConfirmText] = useState("");
  const [taskId, setTaskId] = useState<string | undefined>(undefined);
  const confirmId = useId();

  // Exact-string match: not toLowerCase, not trim, not regex.
  // The operator must reproduce the phrase byte-for-byte.
  const phraseMatches = confirmText === CONFIRM_PHRASE;

  const reset = useCallback(() => {
    setConfirmText("");
  }, []);

  const handleOpenChange = useCallback(
    (next: boolean) => {
      setOpen(next);
      if (!next) reset();
    },
    [reset],
  );

  const handleConfirm = useCallback(() => {
    if (!phraseMatches || upgrade.isPending) return;
    upgrade.mutate(undefined, {
      onSuccess: (data) => {
        const id = typeof data?.task_id === "string" ? data.task_id : "";
        if (!id) {
          toast.error("Upgrade accepted but no task id returned");
          return;
        }
        setTaskId(id);
        reset();
        setOpen(false);
        toast.success("Upgrade started");
      },
      onError: (err) => {
        const msg =
          err instanceof ApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : "Upgrade failed";
        toast.error(msg);
      },
    });
  }, [phraseMatches, reset, upgrade]);

  // Render-nothing fallbacks: error from probe, no payload, or
  // controller says no update is available. The banner must NOT
  // crash the shell when the probe endpoint hiccups.
  if (update.error) return null;
  const data = update.data;
  if (!data || data.available !== true) {
    // Even when the probe doesn't fire the banner, an in-flight
    // upgrade kicked off in this session must keep its progress
    // dialog mounted until terminal.
    return taskId ? (
      <UpgradeProgressDialog
        taskId={taskId}
        onClose={() => setTaskId(undefined)}
      />
    ) : null;
  }

  const releaseNotes =
    typeof data.release_notes === "string" ? data.release_notes : "";

  return (
    <div
      role="region"
      aria-label="Stack upgrade available"
      data-testid="upgrade-banner"
      className="border-b border-[color-mix(in_oklab,var(--color-accent)_35%,transparent)] bg-[color-mix(in_oklab,var(--color-accent)_8%,transparent)] px-4 py-3 sm:px-6"
    >
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-3">
          <ArrowUpCircle
            className="mt-0.5 size-5 shrink-0 text-accent"
            aria-hidden
          />
          <div className="text-sm">
            <div className="font-medium text-fg">
              A new stack release is available
            </div>
            <div className="text-fg-muted">
              {data.current_version ? (
                <>
                  Running{" "}
                  <span
                    className="font-mono"
                    data-testid="upgrade-banner-current"
                  >
                    {data.current_version}
                  </span>
                  {" — latest "}
                </>
              ) : (
                "Latest "
              )}
              <span
                className="font-mono"
                data-testid="upgrade-banner-latest"
              >
                {data.latest_version ?? "unknown"}
              </span>
            </div>
          </div>
        </div>
        <Dialog open={open} onOpenChange={handleOpenChange}>
          <DialogTrigger asChild>
            <Button variant="primary" data-testid="upgrade-banner-trigger">
              Upgrade now
            </Button>
          </DialogTrigger>
          <DialogContent data-testid="upgrade-banner-dialog">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <ArrowUpCircle className="size-5" aria-hidden />
                Upgrade the stack?
              </DialogTitle>
              <DialogDescription>
                The controller will pull the latest release and restart
                services in place. Active users will see brief
                interruptions.
              </DialogDescription>
            </DialogHeader>

            {releaseNotes ? (
              <div className="space-y-1">
                <div className="text-xs font-medium text-fg-muted">
                  Release notes
                </div>
                <pre
                  className="max-h-48 overflow-auto rounded-md border border-border bg-bg-2 p-3 font-mono text-xs leading-relaxed text-fg"
                  data-testid="upgrade-banner-release-notes"
                >
                  {releaseNotes}
                </pre>
              </div>
            ) : null}

            <div className="space-y-2">
              <Label htmlFor={confirmId}>
                Type{" "}
                <span className="font-mono font-semibold text-accent">
                  {CONFIRM_PHRASE}
                </span>{" "}
                to unlock the button:
              </Label>
              <Input
                id={confirmId}
                type="text"
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                data-testid="upgrade-banner-confirm-input"
                aria-describedby={`${confirmId}-help`}
              />
              <p
                id={`${confirmId}-help`}
                className="text-xs text-fg-faint"
              >
                Exact match required (case-sensitive, no surrounding spaces).
              </p>
            </div>

            <DialogFooter>
              <Button
                variant="secondary"
                onClick={() => handleOpenChange(false)}
                disabled={upgrade.isPending}
                data-testid="upgrade-banner-cancel"
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                disabled={!phraseMatches || upgrade.isPending}
                loading={upgrade.isPending}
                onClick={handleConfirm}
                data-testid="upgrade-banner-confirm"
              >
                Confirm — start upgrade
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      {taskId ? (
        <UpgradeProgressDialog
          taskId={taskId}
          onClose={() => setTaskId(undefined)}
        />
      ) : null}
    </div>
  );
}
