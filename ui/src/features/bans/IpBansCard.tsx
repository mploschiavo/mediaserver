import { useState, type FormEvent } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { Ban, Network, Plus } from "lucide-react";
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
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { EmptyState } from "@/components/layout/EmptyState";
import { cn } from "@/lib/cn";
import {
  useAddIpBan,
  useIpBans,
  useRemoveIpBan,
  type IpBan,
} from "./hooks";

function fmtDate(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function fmtUntil(iso?: string): string {
  if (!iso) return "indefinite";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function explain(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Request failed";
}

const IPV4_OCTETS = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/;
const IPV6_LOOSE = /^[0-9a-fA-F:]+$/;

/**
 * Lightweight CIDR validation. Accepts either a bare IP (v4 or v6) or
 * an IP with a `/prefix` suffix; the controller is the source of truth
 * but we surface obvious typos inline before round-tripping.
 */
export function isValidCidr(input: string): boolean {
  const trimmed = input.trim();
  if (!trimmed) return false;
  const [addr, prefixRaw, ...rest] = trimmed.split("/");
  if (rest.length > 0) return false;
  if (!addr) return false;

  const m = addr.match(IPV4_OCTETS);
  let isV4 = false;
  if (m) {
    for (let i = 1; i <= 4; i++) {
      const n = Number(m[i]);
      if (!Number.isFinite(n) || n < 0 || n > 255) return false;
    }
    isV4 = true;
  }
  const isV6 = !isV4 && addr.includes(":") && IPV6_LOOSE.test(addr);
  if (!isV4 && !isV6) return false;

  if (prefixRaw === undefined) return true;
  if (prefixRaw === "") return false;
  const prefix = Number(prefixRaw);
  if (!Number.isInteger(prefix) || prefix < 0) return false;
  return isV4 ? prefix <= 32 : prefix <= 128;
}

export function IpBansCard() {
  const reduce = useReducedMotion();
  const bans = useIpBans();
  const add = useAddIpBan();
  const remove = useRemoveIpBan();
  const [open, setOpen] = useState(false);
  const [cidr, setCidr] = useState("");
  const [reason, setReason] = useState("");
  const [until, setUntil] = useState("");
  const [cidrError, setCidrError] = useState<string | null>(null);
  const [pendingRemove, setPendingRemove] = useState<string | null>(null);

  const reset = () => {
    setCidr("");
    setReason("");
    setUntil("");
    setCidrError(null);
  };

  const handleAdd = (ev: FormEvent) => {
    ev.preventDefault();
    const trimmed = cidr.trim();
    if (!isValidCidr(trimmed)) {
      setCidrError(
        "Enter a valid IP or CIDR range (e.g. 192.168.0.0/24).",
      );
      return;
    }
    setCidrError(null);
    const body = {
      cidr: trimmed,
      reason: reason.trim(),
      ...(until ? { expires_at: new Date(until).toISOString() } : {}),
    };
    add.mutate(body, {
      onSuccess: () => {
        toast.success(`Banned ${body.cidr}`);
        reset();
        setOpen(false);
      },
      onError: (err) => {
        toast.error(`Ban failed: ${explain(err)}`);
      },
    });
  };

  const handleLift = (ban: IpBan) => {
    if (
      typeof window !== "undefined" &&
      !window.confirm(`Lift ban on ${ban.cidr}?`)
    ) {
      return;
    }
    setPendingRemove(ban.cidr);
    remove.mutate(
      { cidr: ban.cidr },
      {
        onSuccess: () => {
          toast.success(`Lifted ban on ${ban.cidr}`);
        },
        onError: (err) => {
          toast.error(`Unban failed: ${explain(err)}`);
        },
        onSettled: () => {
          setPendingRemove(null);
        },
      },
    );
  };

  return (
    <motion.div
      initial={reduce ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
      data-testid="ip-bans-card"
    >
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
          <div className="flex flex-col gap-1.5">
            <CardTitle className="flex items-center gap-2">
              <Network className="size-4 text-fg-muted" aria-hidden />
              Banned IP / CIDR ranges
            </CardTitle>
            <CardDescription>
              Block traffic at the network layer. CIDR notation accepted.
            </CardDescription>
          </div>
          <Dialog
            open={open}
            onOpenChange={(next) => {
              setOpen(next);
              if (!next) reset();
            }}
          >
            <DialogTrigger asChild>
              <Button
                variant="primary"
                size="sm"
                data-testid="ip-ban-add-trigger"
              >
                <Plus aria-hidden />
                Add ban
              </Button>
            </DialogTrigger>
            <DialogContent data-testid="ip-ban-dialog">
              <DialogHeader>
                <DialogTitle>Ban an IP or CIDR</DialogTitle>
                <DialogDescription>
                  Single addresses (`203.0.113.45`) or ranges
                  (`203.0.113.0/24`) are accepted.
                </DialogDescription>
              </DialogHeader>
              <form
                className="flex flex-col gap-4"
                onSubmit={handleAdd}
                aria-label="Add IP ban"
                noValidate
              >
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="ip-ban-cidr">IP / CIDR</Label>
                  <Input
                    id="ip-ban-cidr"
                    name="cidr"
                    autoComplete="off"
                    required
                    placeholder="192.168.0.0/24"
                    value={cidr}
                    onChange={(e) => {
                      setCidr(e.target.value);
                      if (cidrError) setCidrError(null);
                    }}
                    aria-invalid={cidrError ? "true" : undefined}
                    aria-describedby={
                      cidrError ? "ip-ban-cidr-error" : undefined
                    }
                    data-testid="ip-ban-cidr-input"
                  />
                  {cidrError ? (
                    <p
                      id="ip-ban-cidr-error"
                      role="alert"
                      className="text-xs text-danger"
                      data-testid="ip-ban-cidr-error"
                    >
                      {cidrError}
                    </p>
                  ) : null}
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="ip-ban-reason">Reason</Label>
                  <textarea
                    id="ip-ban-reason"
                    name="reason"
                    rows={3}
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    className={cn(
                      "flex w-full rounded-md border border-input bg-bg-1 px-3 py-2 text-base sm:text-sm text-fg shadow-sm",
                      "transition-colors duration-[var(--duration-fast)] ease-[var(--ease-out)] placeholder:text-fg-faint",
                      "focus-visible:border-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-bg",
                      "disabled:cursor-not-allowed disabled:opacity-50",
                    )}
                    placeholder="Why is this range being banned?"
                    data-testid="ip-ban-reason-input"
                  />
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="ip-ban-until">
                    Until <span className="text-fg-faint">(optional)</span>
                  </Label>
                  <Input
                    id="ip-ban-until"
                    name="until"
                    type="datetime-local"
                    value={until}
                    onChange={(e) => setUntil(e.target.value)}
                    data-testid="ip-ban-until-input"
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
                    variant="danger"
                    loading={add.isPending}
                    disabled={!cidr.trim()}
                    data-testid="ip-ban-submit"
                  >
                    <Ban aria-hidden />
                    Ban IP
                  </Button>
                </DialogFooter>
              </form>
            </DialogContent>
          </Dialog>
        </CardHeader>
        <CardContent className="p-0">
          {bans.isLoading ? (
            <div
              className="flex flex-col gap-2 p-6"
              data-testid="ip-bans-loading"
            >
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : bans.error ? (
            <div
              role="alert"
              data-testid="ip-bans-error"
              className="px-6 py-6 text-sm text-danger"
            >
              {bans.error.message}
            </div>
          ) : !bans.data || bans.data.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={Network}
                title="No IP bans"
                description="No IP or CIDR ranges are currently banned."
              />
            </div>
          ) : (
            <Table data-testid="ip-bans-table">
              <TableHeader>
                <TableRow>
                  <TableHead>CIDR</TableHead>
                  <TableHead>Reason</TableHead>
                  <TableHead>Banned at</TableHead>
                  <TableHead>Until</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {bans.data.map((b) => (
                  <TableRow
                    key={b.cidr}
                    data-testid={`ip-ban-row-${b.cidr}`}
                  >
                    <TableCell className="font-mono text-fg">
                      {b.cidr}
                    </TableCell>
                    <TableCell className="text-fg-muted">
                      {b.reason || "—"}
                    </TableCell>
                    <TableCell className="tabular-nums text-fg-muted">
                      {fmtDate(b.banned_at)}
                    </TableCell>
                    <TableCell className="tabular-nums text-fg-muted">
                      {fmtUntil(b.expires_at)}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => handleLift(b)}
                        loading={
                          remove.isPending && pendingRemove === b.cidr
                        }
                        disabled={remove.isPending}
                        data-testid={`ip-ban-lift-${b.cidr}`}
                        aria-label={`Lift ban on ${b.cidr}`}
                      >
                        Lift ban
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </motion.div>
  );
}
