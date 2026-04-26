import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Eye, EyeOff, KeyRound, Sparkles, Copy, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { fetcher } from "@/api/client";
import { toast } from "sonner";

interface ResetPasswordDialogProps {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  userId: string;
  username: string;
}

interface ResetResponse {
  user_id?: string;
  password_ticket?: string;
  ticket_expires_at?: string;
  // Legacy shape, just in case the controller is mid-rollout:
  generated_password?: string;
}

/**
 * Two-mode password reset dialog.
 *
 *   * **Type a new password** (default) — admin enters the password
 *     directly. Backend stores it, returns no ticket. Admin already
 *     knows the value and can communicate it out-of-band.
 *
 *   * **Generate random** — admin clicks "Generate" instead. Backend
 *     mints a random password + a single-use ticket. We immediately
 *     consume the ticket via ``GET /api/password-tickets/{id}`` so
 *     the plaintext appears once on screen with a copy-to-clipboard
 *     button. The ticket can only be redeemed once and expires
 *     server-side (default 5 min); after that the admin would have
 *     to reset again.
 *
 * Why this dialog exists: the previous "Reset password" button on
 * the user drawer fired the mutation with no body, which triggered
 * the random-password path on the backend. The plaintext was minted
 * into a ticket but the UI never consumed it — the operator got
 * "Password reset issued" toast and a logged-out user with no way
 * to know the new password. Both modes here surface the new value
 * before the dialog closes.
 */
export function ResetPasswordDialog({
  open,
  onOpenChange,
  userId,
  username,
}: ResetPasswordDialogProps) {
  const [mode, setMode] = useState<"type" | "generate">("type");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [generatedValue, setGeneratedValue] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const reset = useMutation({
    mutationFn: async (body: { password?: string }) => {
      const res = await fetcher<ResetResponse>(
        `api/users/${encodeURIComponent(userId)}/reset-password`,
        {
          method: "POST",
          body: JSON.stringify(body),
        },
      );
      // If the response includes a ticket, consume it immediately
      // so we can show the plaintext once.
      if (res.password_ticket && !body.password) {
        const ticket = await fetcher<{ password?: string }>(
          `api/password-tickets/${encodeURIComponent(res.password_ticket)}`,
        );
        if (ticket.password) {
          return { ...res, generated_password: ticket.password };
        }
      }
      return res;
    },
    onSuccess: (data) => {
      if (data.generated_password) {
        setGeneratedValue(data.generated_password);
        toast.success(
          "Random password generated — shown once below. Copy it before closing this dialog.",
          { duration: 6_000 },
        );
      } else {
        toast.success(`Password reset for ${username}.`);
        onOpenChange(false);
        resetState();
      }
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "Reset failed";
      toast.error(msg);
    },
  });

  const resetState = () => {
    setPassword("");
    setConfirm("");
    setGeneratedValue(null);
    setCopied(false);
    setShowPassword(false);
    setMode("type");
  };

  const handleTypeSubmit = () => {
    if (password.length < 8) {
      toast.error("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirm) {
      toast.error("Passwords don't match.");
      return;
    }
    reset.mutate({ password });
  };

  const handleGenerate = () => {
    reset.mutate({});
  };

  const handleCopy = async () => {
    if (!generatedValue) return;
    try {
      await navigator.clipboard.writeText(generatedValue);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2_000);
    } catch {
      toast.error("Couldn't copy to clipboard. Select the text manually.");
    }
  };

  const handleClose = (next: boolean) => {
    if (!next) resetState();
    onOpenChange(next);
  };

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <KeyRound className="size-4" aria-hidden />
            Reset password for {username}
          </DialogTitle>
          <DialogDescription>
            Either set a password explicitly, or generate a random one
            and copy it from the next screen. The new password
            propagates to Authelia synchronously; downstream services
            (Sonarr / Radarr / qBittorrent) update in the background.
          </DialogDescription>
        </DialogHeader>

        {generatedValue ? (
          <div
            className="flex flex-col gap-3"
            data-testid="reset-password-generated-view"
          >
            <p className="text-sm">
              Random password generated. <strong>Copy it now</strong> —
              it won't be shown again. Single-use ticket expires in ~5
              minutes.
            </p>
            <div className="flex items-center gap-2 rounded-md border border-warning/40 bg-warning/10 p-3">
              <code
                className="flex-1 break-all font-mono text-sm"
                data-testid="reset-password-generated-value"
              >
                {generatedValue}
              </code>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={handleCopy}
                data-testid="reset-password-copy"
              >
                {copied ? (
                  <>
                    <Check className="size-3.5" /> Copied
                  </>
                ) : (
                  <>
                    <Copy className="size-3.5" /> Copy
                  </>
                )}
              </Button>
            </div>
            <DialogFooter>
              <Button
                onClick={() => handleClose(false)}
                data-testid="reset-password-close"
              >
                Done
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            <div
              className="flex gap-1 rounded-md border border-border p-1 text-xs"
              role="tablist"
            >
              <ModeTab
                active={mode === "type"}
                onClick={() => setMode("type")}
                testid="reset-password-tab-type"
              >
                Type a password
              </ModeTab>
              <ModeTab
                active={mode === "generate"}
                onClick={() => setMode("generate")}
                testid="reset-password-tab-generate"
              >
                <Sparkles className="size-3" /> Generate random
              </ModeTab>
            </div>

            {mode === "type" ? (
              <form
                className="flex flex-col gap-3"
                onSubmit={(e) => {
                  e.preventDefault();
                  handleTypeSubmit();
                }}
                data-testid="reset-password-type-form"
              >
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="rp-password">New password</Label>
                  <div className="flex gap-1">
                    <Input
                      id="rp-password"
                      type={showPassword ? "text" : "password"}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      autoComplete="new-password"
                      placeholder="At least 8 characters"
                      data-testid="reset-password-input"
                    />
                    <Button
                      type="button"
                      variant="outline"
                      size="icon"
                      onClick={() => setShowPassword(!showPassword)}
                      aria-label={
                        showPassword ? "Hide password" : "Show password"
                      }
                    >
                      {showPassword ? (
                        <EyeOff className="size-3.5" />
                      ) : (
                        <Eye className="size-3.5" />
                      )}
                    </Button>
                  </div>
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="rp-confirm">Confirm</Label>
                  <Input
                    id="rp-confirm"
                    type={showPassword ? "text" : "password"}
                    value={confirm}
                    onChange={(e) => setConfirm(e.target.value)}
                    autoComplete="new-password"
                    placeholder="Re-enter the password"
                    data-testid="reset-password-confirm"
                  />
                </div>
                <DialogFooter>
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => handleClose(false)}
                  >
                    Cancel
                  </Button>
                  <Button
                    type="submit"
                    loading={reset.isPending}
                    data-testid="reset-password-submit"
                  >
                    Reset password
                  </Button>
                </DialogFooter>
              </form>
            ) : (
              <div
                className="flex flex-col gap-3"
                data-testid="reset-password-generate-form"
              >
                <p className="text-sm text-fg-muted">
                  Generate a random 24-character password. The plaintext
                  will be shown once for you to copy. Recommended for
                  service accounts or when you want maximum entropy.
                </p>
                <DialogFooter>
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => handleClose(false)}
                  >
                    Cancel
                  </Button>
                  <Button
                    type="button"
                    onClick={handleGenerate}
                    loading={reset.isPending}
                    data-testid="reset-password-generate-submit"
                  >
                    <Sparkles className="size-3.5" /> Generate &amp; show
                  </Button>
                </DialogFooter>
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function ModeTab({
  active,
  onClick,
  testid,
  children,
}: {
  active: boolean;
  onClick: () => void;
  testid: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={
        "flex flex-1 items-center justify-center gap-1.5 rounded px-2 py-1 text-xs transition-colors " +
        (active
          ? "bg-info/10 text-info"
          : "text-fg-muted hover:bg-bg-2")
      }
      data-testid={testid}
    >
      {children}
    </button>
  );
}
