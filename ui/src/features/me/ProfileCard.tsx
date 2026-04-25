import { motion, useReducedMotion } from "framer-motion";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatRelative } from "@/features/media-integrity/format";
import { useMe, type MeProfile } from "./hooks";

function initials(name: string): string {
  return name
    .split(/\s+/)
    .map((p) => p[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

function displayName(me: MeProfile): string {
  return me.display_name ?? me.username ?? "You";
}

function roleLabel(me: MeProfile): string | undefined {
  const raw =
    typeof me.role === "string"
      ? me.role
      : typeof me.role_slug === "string"
        ? me.role_slug
        : undefined;
  if (!raw) return undefined;
  return raw.charAt(0).toUpperCase() + raw.slice(1);
}

/**
 * Header card for the /me route. Shows avatar, display name, email,
 * role badge, and a "last login" relative timestamp. Consumes the
 * `useMe()` hook and handles loading + error states inline so the
 * rest of the route can keep mounting independently.
 */
export function ProfileCard() {
  const reduce = useReducedMotion();
  const me = useMe();

  return (
    <Card data-testid="profile-card">
      <CardContent className="flex items-center gap-4 p-6">
        {me.isLoading ? (
          <div
            className="flex w-full items-center gap-4"
            data-testid="profile-card-loading"
          >
            <Skeleton className="size-12 rounded-full" />
            <div className="flex flex-col gap-1.5">
              <Skeleton className="h-4 w-40" />
              <Skeleton className="h-3 w-32" />
            </div>
          </div>
        ) : me.error ? (
          <div
            role="alert"
            data-testid="profile-card-error"
            className="text-sm text-danger"
          >
            <p className="font-medium">Failed to load your profile</p>
            <p className="mt-1 text-fg-muted">{me.error.message}</p>
          </div>
        ) : me.data ? (
          <motion.div
            className="flex items-center gap-4"
            initial={reduce ? false : { opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
          >
            <Avatar className="size-12">
              {typeof me.data.avatar_url === "string" && me.data.avatar_url ? (
                <AvatarImage src={me.data.avatar_url} alt="" />
              ) : null}
              <AvatarFallback>{initials(displayName(me.data))}</AvatarFallback>
            </Avatar>
            <div className="flex min-w-0 flex-col gap-0.5">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-base font-semibold text-fg">
                  {displayName(me.data)}
                </span>
                {roleLabel(me.data) ? (
                  <Badge variant="info" data-testid="profile-card-role">
                    {roleLabel(me.data)}
                  </Badge>
                ) : null}
              </div>
              {me.data.email ? (
                <span className="truncate text-sm text-fg-muted">
                  {me.data.email}
                </span>
              ) : null}
              {me.data.last_login_at ? (
                <span
                  className="text-xs text-fg-faint"
                  data-testid="profile-card-last-login"
                >
                  Last sign-in {formatRelative(me.data.last_login_at)}
                </span>
              ) : null}
            </div>
          </motion.div>
        ) : null}
      </CardContent>
    </Card>
  );
}
