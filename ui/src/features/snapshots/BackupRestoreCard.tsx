import { useCallback, useId, useRef, useState } from "react";
import { AlertOctagon, Download, Upload } from "lucide-react";
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
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useDownloadBackup, useRestoreBackup } from "./hooks";

// Operator must type this phrase verbatim before the Confirm
// button unlocks. Matches the contract used by the emergency-revoke
// card in features/emergency-revoke (no trim, no toLowerCase, no
// regex — exact-string match via `===`).
const CONFIRM_PHRASE = "RESTORE";

/**
 * Side-by-side download / restore controls. The restore flow is a
 * two-step confirm: pick a file, then type the literal phrase
 * `RESTORE` to unlock the destructive Confirm button. The phrase
 * compare uses `===` — same exact-string contract as the
 * emergency-revoke break-glass action.
 */
export function BackupRestoreCard() {
  const download = useDownloadBackup();
  const restore = useRestoreBackup();
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [confirmText, setConfirmText] = useState("");
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const confirmId = useId();

  // Exact-string match: not toLowerCase, not trim, not regex.
  const phraseMatches = confirmText === CONFIRM_PHRASE;

  const reset = useCallback(() => {
    setFile(null);
    setConfirmText("");
    if (fileInputRef.current) fileInputRef.current.value = "";
  }, []);

  const handleOpenChange = useCallback(
    (next: boolean) => {
      setOpen(next);
      if (!next) reset();
    },
    [reset],
  );

  const handleDownload = () => {
    download.mutate(undefined, {
      onSuccess: () => toast.success("Backup download started"),
      onError: (err) => {
        const msg =
          err instanceof ApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : "Download failed";
        toast.error(msg);
      },
    });
  };

  const handleConfirm = () => {
    if (!phraseMatches || !file || restore.isPending) return;
    restore.mutate(
      { file },
      {
        onSuccess: (result) => {
          const restored = result.restored?.length ?? 0;
          const errors = result.errors?.length ?? 0;
          if (errors > 0) {
            toast.error(
              `Partial restore: ${restored} ok, ${errors} error${errors === 1 ? "" : "s"}`,
            );
          } else {
            toast.success(
              `Restored ${restored} file${restored === 1 ? "" : "s"}`,
            );
          }
          reset();
          setOpen(false);
        },
        onError: (err) => {
          const msg =
            err instanceof ApiError
              ? err.message
              : err instanceof Error
                ? err.message
                : "Restore failed";
          toast.error(msg);
        },
      },
    );
  };

  return (
    <Card data-testid="backup-restore-card">
      <CardHeader>
        <CardTitle>Backup &amp; restore</CardTitle>
        <CardDescription>
          Export the entire stack configuration to a JSON file, or restore
          a previous backup. Restore overwrites every config — confirm twice.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex flex-col gap-3 sm:flex-row">
          <Button
            variant="secondary"
            onClick={handleDownload}
            disabled={download.isPending}
            loading={download.isPending}
            data-testid="backup-download"
          >
            <Download aria-hidden />
            Download backup
          </Button>

          <Dialog open={open} onOpenChange={handleOpenChange}>
            <Button
              variant="danger"
              onClick={() => setOpen(true)}
              data-testid="backup-restore-trigger"
            >
              <Upload aria-hidden />
              Restore from backup
            </Button>
            <DialogContent data-testid="backup-restore-dialog">
              <DialogHeader>
                <DialogTitle className="flex items-center gap-2 text-danger">
                  <AlertOctagon className="size-5" aria-hidden />
                  Restore from backup?
                </DialogTitle>
                <DialogDescription>
                  This will overwrite every service config that's present in
                  the backup file. Restart affected services after the
                  restore completes.
                </DialogDescription>
              </DialogHeader>

              <div className="space-y-2">
                <Label htmlFor="backup-restore-file">Backup file (.json)</Label>
                <Input
                  id="backup-restore-file"
                  ref={fileInputRef}
                  type="file"
                  accept="application/json,.json"
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                  data-testid="backup-restore-file-input"
                />
              </div>

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
                  data-testid="backup-restore-confirm-input"
                  aria-describedby={`${confirmId}-help`}
                />
                <p id={`${confirmId}-help`} className="text-xs text-fg-faint">
                  Exact match required (case-sensitive, no surrounding spaces).
                </p>
              </div>

              <DialogFooter>
                <Button
                  variant="secondary"
                  onClick={() => handleOpenChange(false)}
                  disabled={restore.isPending}
                  data-testid="backup-restore-cancel"
                >
                  Cancel
                </Button>
                <Button
                  variant="danger"
                  disabled={!phraseMatches || !file || restore.isPending}
                  loading={restore.isPending}
                  onClick={handleConfirm}
                  data-testid="backup-restore-confirm"
                >
                  Confirm — restore everything
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
      </CardContent>
    </Card>
  );
}
