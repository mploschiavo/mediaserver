import { useState } from "react";
import { Download, ShieldAlert, Upload } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api";
import { Badge } from "@/components/ui/badge";
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
import { Skeleton } from "@/components/ui/skeleton";
import { useRegenerateTlsCertificate, useTlsCertificate } from "./hooks";
import { TlsInstallDialog } from "./TlsInstallDialog";

function pickStr(...vals: Array<unknown>): string | undefined {
  for (const v of vals) {
    if (typeof v === "string" && v.length > 0) return v;
  }
  return undefined;
}

function pickStrList(...vals: Array<unknown>): readonly string[] {
  for (const v of vals) {
    if (Array.isArray(v)) {
      const out: string[] = [];
      for (const x of v) if (typeof x === "string") out.push(x);
      if (out.length > 0) return out;
    }
  }
  return [];
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs uppercase tracking-wide text-fg-muted">{label}</dt>
      <dd className="break-all font-mono text-sm text-fg">{value}</dd>
    </div>
  );
}

/**
 * Edge TLS certificate operator surface — describe the current cert,
 * download it (e.g. for trust-store install), regenerate the
 * self-signed fallback (destructive, confirmed via Dialog), and
 * install a custom PEM cert (TlsInstallDialog).
 */
export function TlsCertificateCard() {
  const cert = useTlsCertificate();
  const regenerate = useRegenerateTlsCertificate();
  const [confirmRegen, setConfirmRegen] = useState(false);
  const [installOpen, setInstallOpen] = useState(false);

  const subject = pickStr(cert.data?.subject, cert.data?.subject_cn) ?? "—";
  const issuer = pickStr(cert.data?.issuer) ?? "—";
  const sans = pickStrList(cert.data?.san, cert.data?.sans);
  const validFrom =
    pickStr(cert.data?.valid_from, cert.data?.not_before) ?? "—";
  const validTo =
    pickStr(cert.data?.valid_to, cert.data?.not_after, cert.data?.expires_at) ??
    "—";
  const fingerprint =
    pickStr(cert.data?.fingerprint, cert.data?.fingerprint_sha256) ?? "—";
  const selfSigned = cert.data?.self_signed === true;

  const onConfirmRegenerate = () => {
    setConfirmRegen(false);
    regenerate.mutate(undefined, {
      onSuccess: () => {
        toast.success("Self-signed certificate regenerated.");
      },
      onError: (err) => {
        const msg =
          err instanceof ApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : "Regenerate failed";
        toast.error(msg);
      },
    });
  };

  const onDownload = () => {
    // Direct navigation to the download endpoint preserves cookies +
    // lets the browser handle the file save UX.
    if (typeof window !== "undefined") {
      window.location.assign("/api/tls/certificate/download");
    }
  };

  return (
    <Card data-testid="tls-certificate-card">
      <CardHeader className="flex flex-row items-start justify-between gap-2">
        <div>
          <CardTitle className="flex items-center gap-2">
            TLS certificate
            {selfSigned ? (
              <Badge variant="warning" data-testid="tls-self-signed-badge">
                self-signed
              </Badge>
            ) : null}
          </CardTitle>
          <CardDescription>
            Edge cert presented to clients reaching the controller.
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {cert.error ? (
          <div
            role="alert"
            data-testid="tls-certificate-error"
            className="rounded-md border border-[color-mix(in_oklab,var(--color-danger)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-danger)_10%,transparent)] p-3 text-sm text-danger"
          >
            <p className="font-medium">Failed to load certificate</p>
            <p className="mt-1 text-fg-muted">{cert.error.message}</p>
          </div>
        ) : cert.isLoading ? (
          <div className="space-y-2" data-testid="tls-certificate-loading">
            <Skeleton className="h-4 w-48" />
            <Skeleton className="h-4 w-72" />
            <Skeleton className="h-4 w-64" />
          </div>
        ) : (
          <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Subject" value={subject} />
            <Field label="Issuer" value={issuer} />
            <div className="flex flex-col gap-0.5">
              <dt className="text-xs uppercase tracking-wide text-fg-muted">
                SAN
              </dt>
              <dd className="font-mono text-sm text-fg">
                {sans.length > 0 ? (
                  <div className="flex flex-wrap gap-1">
                    {sans.map((s) => (
                      <span
                        key={s}
                        className="rounded border border-border bg-bg-2 px-1.5 py-0.5 text-xs"
                      >
                        {s}
                      </span>
                    ))}
                  </div>
                ) : (
                  "—"
                )}
              </dd>
            </div>
            <Field label="Valid from" value={validFrom} />
            <Field label="Valid to" value={validTo} />
            <div className="sm:col-span-2">
              <Field label="Fingerprint" value={fingerprint} />
            </div>
          </dl>
        )}

        <div className="flex flex-wrap items-center justify-end gap-2 pt-2">
          <Button
            variant="secondary"
            size="sm"
            onClick={onDownload}
            data-testid="tls-certificate-download"
          >
            <Download className="size-4" aria-hidden />
            Download
          </Button>
          <Button
            variant="danger"
            size="sm"
            onClick={() => setConfirmRegen(true)}
            data-testid="tls-certificate-regenerate"
            disabled={regenerate.isPending}
          >
            <ShieldAlert className="size-4" aria-hidden />
            Regenerate self-signed
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={() => setInstallOpen(true)}
            data-testid="tls-certificate-install"
          >
            <Upload className="size-4" aria-hidden />
            Install custom
          </Button>
        </div>
      </CardContent>

      <Dialog open={confirmRegen} onOpenChange={setConfirmRegen}>
        <DialogContent data-testid="tls-regenerate-dialog">
          <DialogHeader>
            <DialogTitle>Regenerate self-signed certificate?</DialogTitle>
            <DialogDescription>
              This replaces the edge cert with a freshly-generated self-signed
              one. Active TLS connections terminate at the gateway and the
              operator will need to reconnect (and re-trust the new cert) within
              ~5 seconds of confirming.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setConfirmRegen(false)}
              data-testid="tls-regenerate-cancel"
              disabled={regenerate.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="danger"
              size="sm"
              onClick={onConfirmRegenerate}
              data-testid="tls-regenerate-confirm"
              loading={regenerate.isPending}
              disabled={regenerate.isPending}
            >
              Yes, regenerate
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <TlsInstallDialog open={installOpen} onOpenChange={setInstallOpen} />
    </Card>
  );
}
