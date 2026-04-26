import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import * as Avatar from "@radix-ui/react-avatar";
import {
  ChevronDown,
  ExternalLink,
  Heart,
  LogOut,
  UserCircle2,
} from "lucide-react";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { cn } from "@/lib/cn";

interface UserMenuProps {
  name?: string;
  email?: string;
  avatarUrl?: string;
}

const DOCS_URL = "https://github.com/mploschiavo/mediaserver";
const SPONSOR_URL =
  "https://www.paypal.com/donate?hosted_button_id=XKDG7XXVEQK3W";

/**
 * Top-bar account dropdown. Trigger renders the avatar (with
 * initials fallback) and the user's display name. Menu items hand
 * off to /me, the docs site, and a sign-out endpoint.
 */
export function UserMenu({
  name = "Operator",
  email,
  avatarUrl,
}: UserMenuProps) {
  const navigate = useNavigate();
  const [signingOut, setSigningOut] = useState(false);

  const initials = name
    .split(/\s+/)
    .map((part) => part[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase();

  const handleSignOut = async () => {
    if (signingOut) return;
    setSigningOut(true);
    // Best-effort POST so the controller can audit-log; don't block
    // UX on it. When session is already expired the POST returns 401
    // — that used to leave the user stuck (toast errored, button
    // disabled, no redirect). Now: fire-and-forget, then always
    // navigate to Authelia so the cookie clears regardless.
    try {
      await fetch("/api/auth/logout", {
        method: "POST",
        credentials: "include",
      }).catch(() => undefined);
    } catch {
      // swallowed — Authelia is the source of truth for the cookie.
    }
    // Authelia clears its session cookie when you GET its portal
    // root; navigating there is the canonical sign-out flow with
    // ext_authz. Authelia then redirects to the configured
    // ``default_redirection_url`` which lands on the login screen.
    window.location.replace("/app/authelia/?rd=" + encodeURIComponent(window.location.origin));
  };

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger
        className={cn(
          "flex items-center gap-2 rounded-md px-1.5 py-1 text-sm text-fg outline-none transition-colors",
          "hover:bg-bg-2 focus-visible:ring-2 focus-visible:ring-ring",
        )}
        aria-label="Open account menu"
      >
        <Avatar.Root className="inline-flex size-7 items-center justify-center overflow-hidden rounded-full bg-bg-3 align-middle">
          {avatarUrl ? (
            <Avatar.Image
              src={avatarUrl}
              alt={name}
              className="size-full object-cover"
            />
          ) : null}
          <Avatar.Fallback
            className="text-[11px] font-semibold text-fg-muted"
            delayMs={200}
          >
            {initials || "??"}
          </Avatar.Fallback>
        </Avatar.Root>
        <span className="hidden max-w-[140px] truncate font-medium md:inline">
          {name}
        </span>
        <ChevronDown className="size-3.5 text-fg-faint" aria-hidden />
      </DropdownMenu.Trigger>

      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={6}
          className={cn(
            "z-50 min-w-[220px] overflow-hidden rounded-md border border-border bg-popover p-1 text-popover-fg shadow-lg",
            "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95",
          )}
        >
          <div className="px-2 py-2">
            <div className="text-sm font-medium text-fg">{name}</div>
            {email ? (
              <div className="truncate text-xs text-fg-muted">{email}</div>
            ) : null}
          </div>
          <DropdownMenu.Separator className="my-1 h-px bg-border" />
          <DropdownMenu.Item
            onSelect={(e) => {
              e.preventDefault();
              navigate({ to: "/me" });
            }}
            className={menuItemClass}
          >
            <UserCircle2 className="size-4 text-fg-faint" aria-hidden />
            <span>My profile</span>
          </DropdownMenu.Item>
          <DropdownMenu.Separator className="my-1 h-px bg-border" />
          <DropdownMenu.Item asChild className={menuItemClass}>
            <a href={DOCS_URL} target="_blank" rel="noreferrer noopener">
              <ExternalLink className="size-4 text-fg-faint" aria-hidden />
              <span>Documentation</span>
            </a>
          </DropdownMenu.Item>
          <DropdownMenu.Item asChild className={menuItemClass}>
            <a
              href={SPONSOR_URL}
              target="_blank"
              rel="noreferrer noopener"
              data-testid="user-menu-sponsor"
            >
              <Heart className="size-4 text-danger" aria-hidden />
              <span>Support the project</span>
            </a>
          </DropdownMenu.Item>
          <DropdownMenu.Separator className="my-1 h-px bg-border" />
          <DropdownMenu.Item
            onSelect={(e) => {
              e.preventDefault();
              void handleSignOut();
            }}
            disabled={signingOut}
            className={cn(menuItemClass, "text-danger focus:text-danger")}
          >
            <LogOut className="size-4" aria-hidden />
            <span>{signingOut ? "Signing out…" : "Sign out"}</span>
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

const menuItemClass = cn(
  "flex cursor-pointer select-none items-center gap-2 rounded-sm px-2 py-1.5 text-sm text-fg outline-none",
  "data-[highlighted]:bg-bg-2 data-[highlighted]:text-fg",
  "data-[disabled]:pointer-events-none data-[disabled]:opacity-50",
);
