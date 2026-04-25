import { useId, useState, type ChangeEvent } from "react";
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
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { useInstallTlsCertificate } from "./hooks";
import { looksLikeCertPem, looksLikeKeyPem, parseCertPem } from "./pem";

interface TlsInstallDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

interface FileSlot {
  name: string;
  size: number;
  text: string;
}

async function readFileAsText(file: File): Promise<string> {
  // happy-dom's File polyfill in v15 supports `.text()`. Guard it
  // anyway so this stays robust under older runtimes.
  if (typeof file.text === "function") return file.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("read failed"));
    reader.readAsText(file);
  });
}

/**
 * Two-input PEM upload dialog. We do a *minimal* client-side parse
 * (cert + key markers via regex, plus a cheap ASN.1 walk for subject
 * CN + validity dates) so the operator gets a sanity-check preview
 * before submitting. The controller still validates server-side — we
 * never gate the submit on parse success.
 */
export function TlsInstallDialog({
  open,
  onOpenChange,
}: TlsInstallDialogProps) {
  const [cert, setCert] = useState<FileSlot | null>(null);
  const [key, setKey] = useState<FileSlot | null>(null);
  const [errors, setErrors] = useState<{ cert?: string; key?: string }>({});
  const certInputId = useId();
  const keyInputId = useId();
  const install = useInstallTlsCertificate();

  const reset = () => {
    setCert(null);
    setKey(null);
    setErrors({});
  };

  const handleClose = (next: boolean) => {
    if (!next) reset();
    onOpenChange(next);
  };

  const handleCert = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const text = await readFileAsText(file);
    setCert({ name: file.name, size: file.size, text });
    setErrors((prev) => ({
      ...prev,
      cert: looksLikeCertPem(text)
        ? undefined
        : "File does not contain a CERTIFICATE PEM marker.",
    }));
  };

  const handleKey = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const text = await readFileAsText(file);
    setKey({ name: file.name, size: file.size, text });
    setErrors((prev) => ({
      ...prev,
      key: looksLikeKeyPem(text)
        ? undefined
        : "File does not contain a KEY PEM marker.",
    }));
  };

  const summary = cert ? parseCertPem(cert.text) : {};
  const canSubmit =
    cert !== null &&
    key !== null &&
    !errors.cert &&
    !errors.key &&
    !install.isPending;

  const onSubmit = () => {
    if (!cert || !key) return;
    install.mutate(
      { cert_pem: cert.text, key_pem: key.text },
      {
        onSuccess: () => {
          toast.success("TLS certificate installed.");
          handleClose(false);
        },
        onError: (err) => {
          const msg =
            err instanceof ApiError
              ? err.message
              : err instanceof Error
                ? err.message
                : "Install failed";
          toast.error(msg);
        },
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent data-testid="tls-install-dialog">
        <DialogHeader>
          <DialogTitle>Install TLS certificate</DialogTitle>
          <DialogDescription>
            Upload a PEM-encoded certificate and matching private key. The
            controller validates and reloads the gateway after install.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4 py-2">
          <div className="flex flex-col gap-2">
            <Label htmlFor={certInputId}>Certificate (.pem)</Label>
            <input
              id={certInputId}
              type="file"
              accept=".pem,.crt,.cert,application/x-pem-file"
              onChange={handleCert}
              data-testid="tls-install-cert-input"
              className="text-sm text-fg file:mr-3 file:rounded file:border file:border-border file:bg-bg-2 file:px-2 file:py-1 file:text-xs file:text-fg"
            />
            {cert ? (
              <div className="text-xs text-fg-muted" data-testid="tls-install-cert-name">
                {cert.name} · {cert.size} bytes
              </div>
            ) : null}
            {errors.cert ? (
              <p
                className="text-xs text-danger"
                role="alert"
                data-testid="tls-install-cert-error"
              >
                {errors.cert}
              </p>
            ) : null}
          </div>

          <div className="flex flex-col gap-2">
            <Label htmlFor={keyInputId}>Private key (.pem)</Label>
            <input
              id={keyInputId}
              type="file"
              accept=".pem,.key,application/x-pem-file"
              onChange={handleKey}
              data-testid="tls-install-key-input"
              className="text-sm text-fg file:mr-3 file:rounded file:border file:border-border file:bg-bg-2 file:px-2 file:py-1 file:text-xs file:text-fg"
            />
            {key ? (
              <div className="text-xs text-fg-muted" data-testid="tls-install-key-name">
                {key.name} · {key.size} bytes
              </div>
            ) : null}
            {errors.key ? (
              <p
                className="text-xs text-danger"
                role="alert"
                data-testid="tls-install-key-error"
              >
                {errors.key}
              </p>
            ) : null}
          </div>

          {cert && !errors.cert ? (
            <div
              className="rounded-md border border-border bg-bg-2 p-3 text-xs"
              data-testid="tls-install-preview"
            >
              <div className="mb-1 font-medium text-fg">Parsed cert</div>
              <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-fg-muted">
                <dt>Subject CN</dt>
                <dd className="font-mono text-fg">
                  {summary.subjectCn ?? "—"}
                </dd>
                <dt>Valid from</dt>
                <dd className="font-mono text-fg">
                  {summary.validFrom ?? "—"}
                </dd>
                <dt>Valid to</dt>
                <dd className="font-mono text-fg">{summary.validTo ?? "—"}</dd>
              </dl>
              {!summary.subjectCn && !summary.validFrom ? (
                <p className="mt-2 text-fg-muted">
                  Could not parse — server will validate on submit.
                </p>
              ) : null}
            </div>
          ) : null}
        </div>

        <DialogFooter>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => handleClose(false)}
            data-testid="tls-install-cancel"
            disabled={install.isPending}
          >
            Cancel
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={onSubmit}
            disabled={!canSubmit}
            loading={install.isPending}
            data-testid="tls-install-submit"
          >
            Install
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
