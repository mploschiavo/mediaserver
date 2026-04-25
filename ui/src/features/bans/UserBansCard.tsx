import { useState, type FormEvent } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { Ban, Plus, UserX } from "lucide-react";
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
  useAddUserBan,
  useRemoveUserBan,
  useUserBans,
  type UserBan,
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

export function UserBansCard() {
  const reduce = useReducedMotion();
  const bans = useUserBans();
  const add = useAddUserBan();
  const remove = useRemoveUserBan();
  const [open, setOpen] = useState(false);
  const [username, setUsername] = useState("");
  const [reason, setReason] = useState("");
  const [until, setUntil] = useState("");
  const [pendingRemove, setPendingRemove] = useState<string | null>(null);

  const reset = () => {
    setUsername("");
    setReason("");
    setUntil("");
  };

  const handleAdd = (ev: FormEvent) => {
    ev.preventDefault();
    const body = {
      username: username.trim(),
      reason: reason.trim(),
      ...(until ? { expires_at: new Date(until).toISOString() } : {}),
    };
    if (!body.username) return;
    add.mutate(body, {
      onSuccess: () => {
        toast.success(`Banned ${body.username}`);
        reset();
        setOpen(false);
      },
      onError: (err) => {
        toast.error(`Ban failed: ${explain(err)}`);
      },
    });
  };

  const handleLift = (ban: UserBan) => {
    if (
      typeof window !== "undefined" &&
      !window.confirm(`Lift ban on ${ban.username}?`)
    ) {
      return;
    }
    setPendingRemove(ban.username);
    remove.mutate(
      { username: ban.username },
      {
        onSuccess: () => {
          toast.success(`Lifted ban on ${ban.username}`);
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
      data-testid="user-bans-card"
    >
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
          <div className="flex flex-col gap-1.5">
            <CardTitle className="flex items-center gap-2">
              <UserX className="size-4 text-fg-muted" aria-hidden />
              Banned users
            </CardTitle>
            <CardDescription>
              Block specific accounts across every provider.
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
                data-testid="user-ban-add-trigger"
              >
                <Plus aria-hidden />
                Add ban
              </Button>
            </DialogTrigger>
            <DialogContent data-testid="user-ban-dialog">
              <DialogHeader>
                <DialogTitle>Ban a user</DialogTitle>
                <DialogDescription>
                  Bans propagate to every connected provider. Optional expiry
                  lifts the ban automatically.
                </DialogDescription>
              </DialogHeader>
              <form
                className="flex flex-col gap-4"
                onSubmit={handleAdd}
                aria-label="Add user ban"
              >
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="user-ban-username">Username</Label>
                  <Input
                    id="user-ban-username"
                    name="username"
                    autoComplete="off"
                    required
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    data-testid="user-ban-username-input"
                  />
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="user-ban-reason">Reason</Label>
                  <textarea
                    id="user-ban-reason"
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
                    placeholder="Why is this account being banned?"
                    data-testid="user-ban-reason-input"
                  />
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="user-ban-until">
                    Until <span className="text-fg-faint">(optional)</span>
                  </Label>
                  <Input
                    id="user-ban-until"
                    name="until"
                    type="datetime-local"
                    value={until}
                    onChange={(e) => setUntil(e.target.value)}
                    data-testid="user-ban-until-input"
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
                    disabled={!username.trim()}
                    data-testid="user-ban-submit"
                  >
                    <Ban aria-hidden />
                    Ban user
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
              data-testid="user-bans-loading"
            >
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : bans.error ? (
            <div
              role="alert"
              data-testid="user-bans-error"
              className="px-6 py-6 text-sm text-danger"
            >
              {bans.error.message}
            </div>
          ) : !bans.data || bans.data.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={UserX}
                title="No user bans"
                description="No accounts are currently banned."
              />
            </div>
          ) : (
            <Table data-testid="user-bans-table">
              <TableHeader>
                <TableRow>
                  <TableHead>Username</TableHead>
                  <TableHead>Reason</TableHead>
                  <TableHead>Banned at</TableHead>
                  <TableHead>Until</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {bans.data.map((b) => (
                  <TableRow
                    key={b.username}
                    data-testid={`user-ban-row-${b.username}`}
                  >
                    <TableCell className="font-medium text-fg">
                      {b.username}
                    </TableCell>
                    <TableCell className="text-fg-muted">
                      {b.reason || b.reason_detail || "—"}
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
                          remove.isPending && pendingRemove === b.username
                        }
                        disabled={remove.isPending}
                        data-testid={`user-ban-lift-${b.username}`}
                        aria-label={`Lift ban on ${b.username}`}
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
