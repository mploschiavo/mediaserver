import { asArray } from "@/lib/coerce";
import { useEffect, useState, type FormEvent } from "react";
import { Drawer as VaulDrawer } from "vaul";
import {
  ExternalLink,
  KeyRound,
  ScrollText,
  ShieldAlert,
  UserX,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/cn";
import { formatRelative } from "@/features/media-integrity/format";
import { ResetPasswordDialog } from "./ResetPasswordDialog";
import { useMe } from "@/features/me/hooks";
import { Link } from "@tanstack/react-router";
import {
  usePatchUser,
  useRevokeUserSession,
  useRevokeUserSessions,
  useSetUserRole,
  useUserLoginHistory,
  useUserSessions,
  type AdminRole,
  type AdminUser,
  type LoginHistoryEntry,
  type UserSession,
} from "./hooks";

interface UserDetailDrawerProps {
  user: AdminUser | null;
  initialTab?: "profile" | "sessions" | "login-history";
  roles?: readonly AdminRole[];
  onClose: () => void;
}

function explain(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}

function userRole(u: AdminUser): string {
  return (u.role_slug ?? u.role ?? "viewer") as string;
}

/**
 * Per-user detail drawer. Slides from the right on tap-or-click and
 * exposes Profile / Sessions / Login history panels plus the link
 * out to /audit-log for that operator's audit trail.
 */
export function UserDetailDrawer({
  user,
  initialTab = "profile",
  roles = [],
  onClose,
}: UserDetailDrawerProps) {
  const open = user !== null;

  return (
    <VaulDrawer.Root
      direction="right"
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <VaulDrawer.Portal>
        <VaulDrawer.Overlay className="fixed inset-0 z-50 bg-[color-mix(in_oklab,var(--color-bg)_70%,transparent)] backdrop-blur-sm" />
        <VaulDrawer.Content
          className={cn(
            "fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col border-l border-border bg-bg-1 outline-none",
          )}
          data-testid="user-detail-drawer"
        >
          {user ? (
            <DrawerBody
              user={user}
              initialTab={initialTab}
              roles={roles}
              onClose={onClose}
            />
          ) : null}
        </VaulDrawer.Content>
      </VaulDrawer.Portal>
    </VaulDrawer.Root>
  );
}

function DrawerBody({
  user,
  initialTab,
  roles,
  onClose,
}: {
  user: AdminUser;
  initialTab: "profile" | "sessions" | "login-history";
  roles: readonly AdminRole[];
  onClose: () => void;
}) {
  const [tab, setTab] = useState<typeof initialTab>(initialTab);
  // Whenever the parent re-opens with a different starting tab,
  // honor it so opening "View login history" lands on that panel.
  useEffect(() => {
    setTab(initialTab);
  }, [initialTab, user.id]);

  return (
    <>
      <header className="flex items-start justify-between gap-3 border-b border-border p-4">
        <div className="flex flex-col gap-1">
          <VaulDrawer.Title className="text-lg font-semibold leading-none tracking-tight">
            {user.display_name ?? user.username}
          </VaulDrawer.Title>
          <VaulDrawer.Description className="text-sm text-fg-muted">
            {user.email ?? user.username}
          </VaulDrawer.Description>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-sm p-1 text-fg-muted [@media(hover:hover)]:hover:text-fg"
          aria-label="Close drawer"
          data-testid="user-detail-close"
        >
          <X className="size-4" aria-hidden />
        </button>
      </header>

      <div className="flex-1 overflow-y-auto p-4">
        <Tabs
          value={tab}
          onValueChange={(v) => setTab(v as typeof tab)}
          className="flex flex-col gap-4"
        >
          <TabsList className="self-start">
            <TabsTrigger value="profile" data-testid="user-tab-profile">
              Profile
            </TabsTrigger>
            <TabsTrigger value="sessions" data-testid="user-tab-sessions">
              Sessions
            </TabsTrigger>
            <TabsTrigger
              value="login-history"
              data-testid="user-tab-login-history"
            >
              Login history
            </TabsTrigger>
          </TabsList>

          <TabsContent value="profile">
            <ProfilePanel user={user} roles={roles} />
          </TabsContent>
          <TabsContent value="sessions">
            <SessionsPanel user={user} />
          </TabsContent>
          <TabsContent value="login-history">
            <LoginHistoryPanel user={user} />
          </TabsContent>
        </Tabs>
      </div>

      <footer className="border-t border-border p-4">
        <a
          href={`/audit-log?actor=${encodeURIComponent(user.username)}`}
          className="inline-flex items-center gap-1.5 text-sm text-info underline-offset-2 [@media(hover:hover)]:hover:underline"
          data-testid="user-audit-history-link"
        >
          <ScrollText aria-hidden className="size-4" />
          View audit history for this user
          <ExternalLink aria-hidden className="size-3" />
        </a>
      </footer>
    </>
  );
}

function ProfilePanel({
  user,
  roles,
}: {
  user: AdminUser;
  roles: readonly AdminRole[];
}) {
  const [email, setEmail] = useState(user.email ?? "");
  const [displayName, setDisplayName] = useState(user.display_name ?? "");
  const [role, setRole] = useState(userRole(user));
  const [resetOpen, setResetOpen] = useState(false);
  const me = useMe();
  const isSelf = me.data?.id === user.id;

  const patch = usePatchUser();
  const setUserRole = useSetUserRole();

  const handleSubmit = (ev: FormEvent) => {
    ev.preventDefault();
    patch.mutate(
      {
        user_id: user.id,
        body: { email, display_name: displayName },
      },
      {
        onSuccess: () => toast.success("Profile saved"),
        onError: (err) =>
          toast.error(`Save failed: ${explain(err, "request failed")}`),
      },
    );
    if (role !== userRole(user)) {
      setUserRole.mutate(
        { user_id: user.id, role_slug: role },
        {
          onError: (err) =>
            toast.error(`Role change failed: ${explain(err, "request failed")}`),
        },
      );
    }
  };

  // Old "Reset password" click-and-go silently rotated the password
  // to a random value the operator could never see — it locked admins
  // out of their own accounts. The new flow opens a dialog with two
  // modes: type-explicitly (admin sets the value) or generate-random
  // (UI consumes the ticket and shows the plaintext once).
  const handleReset = () => setResetOpen(true);

  const roleOptions = roles.length
    ? roles.map((r) => ({ value: r.slug, label: r.name ?? r.slug }))
    : [
        { value: "admin", label: "admin" },
        { value: "operator", label: "operator" },
        { value: "viewer", label: "viewer" },
      ];

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={handleSubmit}
      data-testid="user-profile-form"
    >
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="user-username">Username</Label>
        <Input id="user-username" value={user.username} disabled />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="user-email">Email</Label>
        <Input
          id="user-email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          data-testid="user-email-input"
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="user-display">Display name</Label>
        <Input
          id="user-display"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          data-testid="user-display-input"
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label>Role</Label>
        <Select value={role} onValueChange={setRole}>
          <SelectTrigger data-testid="user-role-trigger">
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

      <div className="flex flex-wrap items-center gap-2 pt-2">
        <Button
          type="submit"
          variant="primary"
          loading={patch.isPending}
          data-testid="user-profile-save"
        >
          Save
        </Button>
        {isSelf ? (
          // The Users-tab reset surface is for admin-on-other.
          // Self-resets always go through /me → ChangePasswordCard
          // which verifies the current password before applying the
          // new one — type-then-walk-away here is what locked admins
          // out previously.
          <Button asChild type="button" variant="secondary">
            <Link
              to="/me"
              data-testid="user-profile-go-to-me-for-self-password"
            >
              <KeyRound aria-hidden /> Change my password (in /me)
            </Link>
          </Button>
        ) : (
          <Button
            type="button"
            variant="secondary"
            onClick={handleReset}
            data-testid="user-profile-reset-password"
          >
            <KeyRound aria-hidden /> Reset password…
          </Button>
        )}
      </div>
      {isSelf ? null : (
        <ResetPasswordDialog
          open={resetOpen}
          onOpenChange={setResetOpen}
          userId={user.id}
          username={user.username}
        />
      )}
    </form>
  );
}

function SessionsPanel({ user }: { user: AdminUser }) {
  const sessions = useUserSessions(user.id);
  const revokeAll = useRevokeUserSessions();
  const revokeOne = useRevokeUserSession();

  const handleRevokeAll = () => {
    revokeAll.mutate(
      { user_id: user.id },
      {
        onSuccess: () => toast.success("All sessions revoked"),
        onError: (err) =>
          toast.error(`Revoke failed: ${explain(err, "request failed")}`),
      },
    );
  };

  const handleRevokeOne = (s: UserSession) => {
    revokeOne.mutate(
      { user_id: user.id, session_id: s.id },
      {
        onSuccess: () => toast.success(`Session ${s.id} revoked`),
        onError: (err) =>
          toast.error(`Revoke failed: ${explain(err, "request failed")}`),
      },
    );
  };

  if (sessions.isLoading) {
    return (
      <div className="space-y-2" data-testid="user-sessions-loading">
        {[0, 1].map((i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    );
  }
  if (sessions.error) {
    return (
      <p
        role="alert"
        className="text-sm text-danger"
        data-testid="user-sessions-error"
      >
        {sessions.error.message}
      </p>
    );
  }

  const list = asArray(sessions.data?.sessions);
  if (list.length === 0) {
    return (
      <p
        className="text-sm text-fg-muted"
        data-testid="user-sessions-empty"
      >
        No active sessions.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3" data-testid="user-sessions">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Device</TableHead>
            <TableHead>IP</TableHead>
            <TableHead>Last seen</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {list.map((s) => (
            <TableRow key={s.id} data-testid={`user-session-${s.id}`}>
              <TableCell className="text-xs">
                {s.device ?? s.user_agent ?? "Unknown"}
              </TableCell>
              <TableCell className="font-mono text-xs">{s.ip ?? "—"}</TableCell>
              <TableCell className="text-xs tabular-nums text-fg-muted">
                {formatRelative(s.last_seen_at ?? s.started_at ?? "")}
              </TableCell>
              <TableCell className="text-right">
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => handleRevokeOne(s)}
                  disabled={revokeOne.isPending}
                  data-testid={`user-session-revoke-${s.id}`}
                  aria-label={`Revoke session ${s.id}`}
                >
                  Revoke
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <div className="flex justify-end">
        <Button
          variant="danger"
          size="sm"
          onClick={handleRevokeAll}
          loading={revokeAll.isPending}
          data-testid="user-sessions-revoke-all"
        >
          <UserX aria-hidden />
          Revoke all sessions
        </Button>
      </div>
    </div>
  );
}

function LoginHistoryPanel({ user }: { user: AdminUser }) {
  const history = useUserLoginHistory(user.id);

  if (history.isLoading) {
    return (
      <div className="space-y-2" data-testid="user-login-history-loading">
        {[0, 1].map((i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    );
  }
  if (history.error) {
    return (
      <p
        role="alert"
        className="text-sm text-danger"
        data-testid="user-login-history-error"
      >
        {history.error.message}
      </p>
    );
  }

  const entries = asArray(history.data?.entries);
  if (entries.length === 0) {
    return (
      <p
        className="text-sm text-fg-muted"
        data-testid="user-login-history-empty"
      >
        No login history yet.
      </p>
    );
  }

  return (
    <ul
      className="flex flex-col gap-2 text-sm"
      data-testid="user-login-history"
    >
      {entries.map((e: LoginHistoryEntry, idx) => (
        <li
          key={`${e.ts ?? idx}-${e.ip ?? idx}`}
          className="flex flex-col gap-1 rounded-md border border-border bg-bg-1 p-3"
        >
          <div className="flex items-center justify-between gap-2">
            <span className="font-mono text-xs">{e.ip ?? "—"}</span>
            <span className="text-xs tabular-nums text-fg-muted">
              {formatRelative(e.ts ?? "")}
            </span>
          </div>
          <div className="flex items-center justify-between gap-2 text-xs text-fg-muted">
            <span className="truncate">{e.user_agent ?? ""}</span>
            <span className="flex items-center gap-1">
              {e.is_first_seen ? (
                <Badge variant="warning" className="gap-1">
                  <ShieldAlert aria-hidden className="size-3" />
                  first seen
                </Badge>
              ) : null}
              {e.result ? (
                <Badge
                  variant={e.result === "success" ? "success" : "danger"}
                >
                  {e.result}
                </Badge>
              ) : null}
            </span>
          </div>
        </li>
      ))}
    </ul>
  );
}
