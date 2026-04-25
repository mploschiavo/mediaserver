import { useCallback, useId, useState } from "react";
import { AlertOctagon } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
import { useEmergencyRevokeAll } from "./hooks";

// The exact phrase the operator must type before the Confirm
// button unlocks. This is a contract — it is compared with `===`
// against the input value, no trim, no toLowerCase, no regex.
// Changing this string is a UX-breaking change.
const CONFIRM_PHRASE = "REVOKE ALL";

/**
 * Break-glass card for the security/users surface. Renders a
 * destructive trigger that opens a Radix Dialog explaining the
 * blast radius (every session, every provider, audit-logged) and
 * gates the Confirm button on the operator typing `CONFIRM_PHRASE`
 * verbatim into a confirmation input. An optional reason field is
 * sent to the controller for the audit trail.
 *
 * Style mirrors `features/media-integrity/NeedsReviewPanel` —
 * tinted card chrome, lucide icon next to the title, button
 * variant chosen from the shared `Button` cva contract.
 */
export function EmergencyRevokeCard() {
  const [open, setOpen] = useState(false);
  const [confirmText, setConfirmText] = useState("");
  const [reason, setReason] = useState("");
  const confirmId = useId();
  const reasonId = useId();
  const revoke = useEmergencyRevokeAll();

  // Exact-string match: not toLowerCase, not trim, not regex.
  // The operator must reproduce the phrase byte-for-byte.
  const phraseMatches = confirmText === CONFIRM_PHRASE;

  const reset = useCallback(() => {
    setConfirmText("");
    setReason("");
  }, []);

  const handleOpenChange = useCallback(
    (next: boolean) => {
      setOpen(next);
      if (!next) reset();
    },
    [reset],
  );

  const handleConfirm = useCallback(() => {
    if (!phraseMatches || revoke.isPending) return;
    revoke.mutate(
      { reason: reason.trim() },
      {
        onSuccess: () => {
          toast.success("Emergency revoke complete — every session terminated");
          reset();
          setOpen(false);
        },
        onError: (err) => {
          // 401 is intentionally not handled here — the global auth
          // event listener wired in the layout shell catches the
          // ApiError emitted by the fetcher and redirects.
          const msg =
            err instanceof ApiError
              ? err.message
              : err instanceof Error
                ? err.message
                : "Emergency revoke failed";
          toast.error(msg);
        },
      },
    );
  }, [phraseMatches, reason, reset, revoke]);

  return (
    <Card
      className="border-[color-mix(in_oklab,var(--color-danger)_45%,transparent)] bg-[color-mix(in_oklab,var(--color-danger)_4%,transparent)]"
      data-testid="emergency-revoke-card"
    >
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-danger">
          <AlertOctagon className="size-4" aria-hidden />
          Emergency revoke
        </CardTitle>
        <CardDescription>
          Terminates every active session across every provider in the
          deployment. Every user — including you — is signed out and must
          re-authenticate. The action is recorded in the audit log.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Dialog open={open} onOpenChange={handleOpenChange}>
          <DialogTrigger asChild>
            <Button variant="danger" data-testid="emergency-revoke-trigger">
              <AlertOctagon aria-hidden />
              Revoke all sessions
            </Button>
          </DialogTrigger>
          <DialogContent data-testid="emergency-revoke-dialog">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2 text-danger">
                <AlertOctagon className="size-5" aria-hidden />
                Revoke every session?
              </DialogTitle>
              <DialogDescription>
                This is a break-glass action. It will:
              </DialogDescription>
            </DialogHeader>
            <ul className="ml-5 list-disc space-y-1 text-sm text-fg-muted">
              <li>
                Terminate every live session on every provider (controller,
                Authelia, Jellyfin, ...).
              </li>
              <li>Force every user — including you — to sign in again.</li>
              <li>
                Write an <code>emergency_revoke_all</code> entry to the audit
                log with your identity and the current timestamp.
              </li>
            </ul>

            <div className="space-y-2">
              <Label htmlFor={confirmId}>
                Type{" "}
                <span className="font-mono font-semibold text-danger">
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
                data-testid="emergency-revoke-confirm-input"
                aria-describedby={`${confirmId}-help`}
              />
              <p
                id={`${confirmId}-help`}
                className="text-xs text-fg-faint"
              >
                Exact match required (case-sensitive, no surrounding spaces).
              </p>
            </div>

            <div className="space-y-2">
              <Label htmlFor={reasonId}>
                Reason (optional, written to the audit log):
              </Label>
              <Input
                id={reasonId}
                type="text"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Active credential leak via ..."
                data-testid="emergency-revoke-reason-input"
              />
            </div>

            <DialogFooter>
              <Button
                variant="secondary"
                onClick={() => handleOpenChange(false)}
                disabled={revoke.isPending}
                data-testid="emergency-revoke-cancel"
              >
                Cancel
              </Button>
              <Button
                variant="danger"
                disabled={!phraseMatches || revoke.isPending}
                loading={revoke.isPending}
                onClick={handleConfirm}
                data-testid="emergency-revoke-confirm"
              >
                Confirm — revoke everything
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </CardContent>
    </Card>
  );
}
