import { asArray } from "@/lib/coerce";
import { useState, type FormEvent } from "react";
import { Copy, Mail, Plus, Trash2 } from "lucide-react";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/layout/EmptyState";
import { formatRelative } from "@/features/media-integrity/format";
import {
  useCreateInvite,
  useInvites,
  useRevokeInvite,
  type AdminInvite,
  type AdminRole,
} from "./hooks";

interface InvitesCardProps {
  roles?: readonly AdminRole[];
}

function explain(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}

function inviteUrl(invite: AdminInvite): string {
  if (invite.invite_url) return invite.invite_url;
  if (invite.url) return invite.url;
  if (invite.token && typeof window !== "undefined") {
    return `${window.location.origin}/invite/accept?token=${encodeURIComponent(invite.token)}`;
  }
  return invite.token ?? "";
}

async function copyToClipboard(text: string) {
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      await navigator.clipboard.writeText(text);
      toast.success("Copied invite link");
      return;
    }
  } catch {
    // fall through to error toast below.
  }
  toast.error("Copy failed — clipboard unavailable");
}

export function InvitesCard({ roles = [] }: InvitesCardProps) {
  const invites = useInvites();
  const list = asArray(invites.data?.invites);

  return (
    <Card data-testid="invites-card">
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
        <div className="flex flex-col gap-1.5">
          <CardTitle className="flex items-center gap-2">
            <Mail aria-hidden className="size-4 text-fg-muted" />
            Invitations
          </CardTitle>
          <CardDescription>
            Pending invites. Each invite is single-use and expires.
          </CardDescription>
        </div>
        <CreateInviteDialog roles={roles} />
      </CardHeader>
      <CardContent className="p-0">
        {invites.isLoading ? (
          <div className="space-y-2 p-6" data-testid="invites-loading">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : invites.error ? (
          <p
            role="alert"
            className="px-6 py-4 text-sm text-danger"
            data-testid="invites-error"
          >
            {invites.error.message}
          </p>
        ) : list.length === 0 ? (
          <div className="p-6">
            <EmptyState
              icon={Mail}
              title="No pending invites"
              description="Create an invite link to onboard a teammate."
            />
          </div>
        ) : (
          <ul
            className="divide-y divide-border"
            data-testid="invites-list"
          >
            {list.map((invite) => (
              <InviteRow key={invite.id} invite={invite} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function InviteRow({ invite }: { invite: AdminInvite }) {
  const revoke = useRevokeInvite();
  const url = inviteUrl(invite);
  const isActive = invite.status !== "revoked" && invite.status !== "consumed";

  const handleRevoke = () => {
    revoke.mutate(
      { invite_id: invite.id },
      {
        onSuccess: () => toast.success("Invite revoked"),
        onError: (err) =>
          toast.error(`Revoke failed: ${explain(err, "request failed")}`),
      },
    );
  };

  return (
    <li
      className="flex flex-col gap-2 px-6 py-4 sm:flex-row sm:items-center sm:justify-between"
      data-testid={`invite-row-${invite.id}`}
    >
      <div className="flex flex-col gap-1 min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-fg">
            {invite.email ?? "(no email)"}
          </span>
          {invite.role_slug ? (
            <Badge variant="outline">{invite.role_slug}</Badge>
          ) : null}
          {invite.status ? (
            <Badge variant={isActive ? "success" : "default"}>
              {invite.status}
            </Badge>
          ) : null}
        </div>
        {invite.expires_at ? (
          <span className="text-xs text-fg-muted">
            expires {formatRelative(invite.expires_at)}
          </span>
        ) : null}
        {isActive && url ? (
          <button
            type="button"
            onClick={() => copyToClipboard(url)}
            className="flex items-center gap-1 truncate text-xs text-info underline-offset-2 [@media(hover:hover)]:hover:underline"
            data-testid={`invite-copy-${invite.id}`}
          >
            <Copy aria-hidden className="size-3" />
            <span className="truncate font-mono">{url}</span>
          </button>
        ) : null}
      </div>
      <Button
        size="sm"
        variant="secondary"
        onClick={handleRevoke}
        loading={revoke.isPending}
        data-testid={`invite-revoke-${invite.id}`}
        aria-label={`Revoke invite ${invite.id}`}
      >
        <Trash2 aria-hidden /> Revoke
      </Button>
    </li>
  );
}

function CreateInviteDialog({ roles }: { roles: readonly AdminRole[] }) {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [roleSlug, setRoleSlug] = useState("viewer");
  const create = useCreateInvite();

  const reset = () => {
    setEmail("");
    setRoleSlug("viewer");
  };

  const handleSubmit = (ev: FormEvent) => {
    ev.preventDefault();
    create.mutate(
      {
        ...(email ? { email } : {}),
        role_slug: roleSlug,
      },
      {
        onSuccess: () => {
          toast.success("Invite created");
          reset();
          setOpen(false);
        },
        onError: (err) =>
          toast.error(`Create failed: ${explain(err, "request failed")}`),
      },
    );
  };

  const roleOptions = roles.length
    ? roles.map((r) => ({ value: r.slug, label: r.name ?? r.slug }))
    : [
        { value: "admin", label: "admin" },
        { value: "operator", label: "operator" },
        { value: "viewer", label: "viewer" },
      ];

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button variant="primary" size="sm" data-testid="invite-create-trigger">
          <Plus aria-hidden /> Create invite
        </Button>
      </DialogTrigger>
      <DialogContent data-testid="invite-dialog">
        <DialogHeader>
          <DialogTitle>Create invite</DialogTitle>
          <DialogDescription>
            Generates a single-use invite link for an external user.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={handleSubmit}
          aria-label="Create invite"
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="invite-email">
              Email <span className="text-fg-faint">(optional)</span>
            </Label>
            <Input
              id="invite-email"
              type="email"
              autoComplete="off"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              data-testid="invite-email"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="invite-role">Role</Label>
            <Select value={roleSlug} onValueChange={setRoleSlug}>
              <SelectTrigger id="invite-role" data-testid="invite-role">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {roleOptions.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
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
              loading={create.isPending}
              data-testid="invite-submit"
            >
              Create
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
