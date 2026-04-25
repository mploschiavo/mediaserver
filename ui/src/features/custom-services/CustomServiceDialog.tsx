import { useState, type FormEvent } from "react";
import { Boxes } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/cn";
import { useDefineCustomService } from "./hooks";

interface CustomServiceDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const NAME_RE = /^[a-z0-9-]+$/;
// Loose registry-ref check: optional registry/host, optional namespace,
// repository, and optional `:tag` or `@sha256:...` suffix. The
// controller is the source of truth — we only catch obvious typos.
const IMAGE_RE =
  /^[a-z0-9]+(?:[._-][a-z0-9]+)*(?:\/[a-z0-9]+(?:[._-][a-z0-9]+)*)*(?::[A-Za-z0-9_.-]+|@sha256:[a-f0-9]{64})?$/;

export function isValidServiceName(input: string): boolean {
  const trimmed = input.trim();
  return trimmed.length > 0 && NAME_RE.test(trimmed);
}

export function isValidImageRef(input: string): boolean {
  const trimmed = input.trim();
  return trimmed.length > 0 && IMAGE_RE.test(trimmed);
}

function explain(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Request failed";
}

const TEXTAREA_CN = cn(
  "flex w-full rounded-md border border-input bg-bg-1 px-3 py-2 text-base sm:text-sm text-fg shadow-sm",
  "transition-colors duration-[var(--duration-fast)] ease-[var(--ease-out)] placeholder:text-fg-faint",
  "focus-visible:border-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-bg",
  "disabled:cursor-not-allowed disabled:opacity-50",
);

/**
 * Dialog form for `POST /api/custom-service`. Used from
 * `CustomServiceCard`. Controlled open state lets the card reset the
 * form between launches.
 */
export function CustomServiceDialog({
  open,
  onOpenChange,
}: CustomServiceDialogProps) {
  const define = useDefineCustomService();

  const [name, setName] = useState("");
  const [image, setImage] = useState("");
  const [ports, setPorts] = useState("");
  const [volumes, setVolumes] = useState("");
  const [env, setEnv] = useState("");
  const [healthcheck, setHealthcheck] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [imageError, setImageError] = useState<string | null>(null);

  const reset = () => {
    setName("");
    setImage("");
    setPorts("");
    setVolumes("");
    setEnv("");
    setHealthcheck("");
    setNameError(null);
    setImageError(null);
  };

  const handleSubmit = (ev: FormEvent) => {
    ev.preventDefault();
    const trimmedName = name.trim();
    const trimmedImage = image.trim();
    let bad = false;
    if (!isValidServiceName(trimmedName)) {
      setNameError("Lowercase letters, digits, and dashes only.");
      bad = true;
    } else {
      setNameError(null);
    }
    if (!isValidImageRef(trimmedImage)) {
      setImageError("Enter a registry reference (e.g. linuxserver/foo:latest).");
      bad = true;
    } else {
      setImageError(null);
    }
    if (bad) return;

    const body = {
      name: trimmedName,
      image: trimmedImage,
      ...(ports.trim() ? { ports: ports.trim() } : {}),
      ...(volumes.trim() ? { volumes: volumes.trim() } : {}),
      ...(env.trim() ? { env: env.trim() } : {}),
      ...(healthcheck.trim() ? { healthcheck: healthcheck.trim() } : {}),
    };
    define.mutate(body, {
      onSuccess: () => {
        toast.success(`Defined custom service "${trimmedName}"`);
        reset();
        onOpenChange(false);
      },
      onError: (err) => {
        toast.error(`Define failed: ${explain(err)}`);
      },
    });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) reset();
      }}
    >
      <DialogContent data-testid="custom-service-dialog">
        <DialogHeader>
          <DialogTitle>Define custom service</DialogTitle>
          <DialogDescription>
            Register a non-standard service the controller should manage.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={handleSubmit}
          aria-label="Define custom service"
          noValidate
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="custom-service-name">Service name</Label>
            <Input
              id="custom-service-name"
              name="name"
              autoComplete="off"
              required
              placeholder="my-service"
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                if (nameError) setNameError(null);
              }}
              aria-invalid={nameError ? "true" : undefined}
              aria-describedby={nameError ? "custom-service-name-error" : undefined}
              data-testid="custom-service-name-input"
            />
            {nameError ? (
              <p
                id="custom-service-name-error"
                role="alert"
                className="text-xs text-danger"
                data-testid="custom-service-name-error"
              >
                {nameError}
              </p>
            ) : null}
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="custom-service-image">Container image</Label>
            <Input
              id="custom-service-image"
              name="image"
              autoComplete="off"
              required
              placeholder="linuxserver/foo:latest"
              value={image}
              onChange={(e) => {
                setImage(e.target.value);
                if (imageError) setImageError(null);
              }}
              aria-invalid={imageError ? "true" : undefined}
              aria-describedby={
                imageError ? "custom-service-image-error" : undefined
              }
              data-testid="custom-service-image-input"
            />
            {imageError ? (
              <p
                id="custom-service-image-error"
                role="alert"
                className="text-xs text-danger"
                data-testid="custom-service-image-error"
              >
                {imageError}
              </p>
            ) : null}
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="custom-service-ports">
              Port mapping <span className="text-fg-faint">(optional)</span>
            </Label>
            <Input
              id="custom-service-ports"
              name="ports"
              autoComplete="off"
              placeholder="8080:80"
              value={ports}
              onChange={(e) => setPorts(e.target.value)}
              data-testid="custom-service-ports-input"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="custom-service-volumes">
              Volumes <span className="text-fg-faint">(optional, one per line)</span>
            </Label>
            <textarea
              id="custom-service-volumes"
              name="volumes"
              rows={2}
              value={volumes}
              onChange={(e) => setVolumes(e.target.value)}
              className={TEXTAREA_CN}
              placeholder="/srv/data:/data"
              data-testid="custom-service-volumes-input"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="custom-service-env">
              Environment <span className="text-fg-faint">(optional, KEY=value per line)</span>
            </Label>
            <textarea
              id="custom-service-env"
              name="env"
              rows={2}
              value={env}
              onChange={(e) => setEnv(e.target.value)}
              className={TEXTAREA_CN}
              placeholder="TZ=UTC"
              data-testid="custom-service-env-input"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="custom-service-healthcheck">
              Healthcheck <span className="text-fg-faint">(optional)</span>
            </Label>
            <Input
              id="custom-service-healthcheck"
              name="healthcheck"
              autoComplete="off"
              placeholder="curl -f http://localhost:80/"
              value={healthcheck}
              onChange={(e) => setHealthcheck(e.target.value)}
              data-testid="custom-service-healthcheck-input"
            />
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="secondary">
                Cancel
              </Button>
            </DialogClose>
            <Button
              type="submit"
              variant="primary"
              loading={define.isPending}
              disabled={!name.trim() || !image.trim()}
              data-testid="custom-service-submit"
            >
              <Boxes aria-hidden />
              Define service
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
