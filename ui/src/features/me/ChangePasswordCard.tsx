import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Eye, EyeOff, KeyRound } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { fetcher } from "@/api/client";
import { toast } from "sonner";
import { useMe } from "./hooks";

/**
 * Self-service password change for the signed-in user.
 *
 * Industry pattern: avatar menu → /me → Security card → Change
 * password (current + new + confirm). Mirrors GitHub / Google /
 * Notion / Linear conventions.
 *
 * Backend wiring: today this routes through
 * ``POST /api/users/{me_id}/reset-password`` — the user_write_service
 * gates the call with ``@requires_self_or_admin`` so the caller's
 * own user_id is authorised. The "current password" field is
 * captured but NOT yet verified server-side (the reset endpoint
 * doesn't take it). Verifying current-password before allowing the
 * change is a defence-in-depth follow-up that adds a dedicated
 * ``/api/me/change-password`` endpoint with old-password check —
 * tracked in memory/project_pending_followups.md.
 *
 * After the change, the new password propagates to Authelia
 * synchronously and to downstream service admins (Sonarr / Radarr /
 * qBittorrent) in the background.
 */
export function ChangePasswordCard() {
  const me = useMe();
  const [currentPwd, setCurrentPwd] = useState("");
  const [newPwd, setNewPwd] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showNew, setShowNew] = useState(false);

  const change = useMutation({
    mutationFn: async (body: { password: string }) => {
      const id = me.data?.id;
      if (!id) throw new Error("Not signed in");
      return fetcher(
        `api/users/${encodeURIComponent(String(id))}/reset-password`,
        {
          method: "POST",
          body: JSON.stringify(body),
        },
      );
    },
    onSuccess: () => {
      toast.success(
        "Password updated. New password is live for Authelia immediately; downstream service admins update within ~30s.",
      );
      setCurrentPwd("");
      setNewPwd("");
      setConfirm("");
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "Change failed";
      toast.error(msg);
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (newPwd.length < 8) {
      toast.error("New password must be at least 8 characters.");
      return;
    }
    if (newPwd !== confirm) {
      toast.error("Passwords don't match.");
      return;
    }
    if (currentPwd === newPwd) {
      toast.error("New password must differ from the current one.");
      return;
    }
    change.mutate({ password: newPwd });
  };

  return (
    <Card data-testid="change-password-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <KeyRound aria-hidden className="size-4" />
          Change password
        </CardTitle>
        <CardDescription>
          Update the password for your dashboard sign-in. Authelia
          accepts the new password immediately; downstream service
          admins (Sonarr / Radarr / qBittorrent) propagate within
          ~30 seconds.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="flex max-w-md flex-col gap-3"
          onSubmit={handleSubmit}
          data-testid="change-password-form"
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cpw-current">Current password</Label>
            <Input
              id="cpw-current"
              type="password"
              value={currentPwd}
              onChange={(e) => setCurrentPwd(e.target.value)}
              autoComplete="current-password"
              data-testid="change-password-current"
            />
            <span className="text-[11px] text-fg-faint">
              Server-side verification of the current password is a
              follow-up (today the field is required client-side only).
            </span>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cpw-new">New password</Label>
            <div className="flex gap-1">
              <Input
                id="cpw-new"
                type={showNew ? "text" : "password"}
                value={newPwd}
                onChange={(e) => setNewPwd(e.target.value)}
                autoComplete="new-password"
                placeholder="At least 8 characters"
                data-testid="change-password-new"
              />
              <Button
                type="button"
                variant="outline"
                size="icon"
                onClick={() => setShowNew(!showNew)}
                aria-label={showNew ? "Hide new password" : "Show new password"}
              >
                {showNew ? (
                  <EyeOff className="size-3.5" />
                ) : (
                  <Eye className="size-3.5" />
                )}
              </Button>
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cpw-confirm">Confirm new password</Label>
            <Input
              id="cpw-confirm"
              type={showNew ? "text" : "password"}
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              autoComplete="new-password"
              data-testid="change-password-confirm"
            />
          </div>
          <div className="flex justify-end">
            <Button
              type="submit"
              loading={change.isPending}
              disabled={!currentPwd || !newPwd || !confirm}
              data-testid="change-password-submit"
            >
              Update password
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
