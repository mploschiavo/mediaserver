import { Link } from "@tanstack/react-router";
import {
  Globe2,
  KeyRound,
  PlayCircle,
  Rocket,
  Users,
} from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";

/**
 * Minimalist post-install quick-start. Three cards, in priority
 * order, that surface ONLY the actions a fresh-deploy operator
 * actually needs to do — everything else is optional and tucked
 * into the per-feature pages.
 *
 *   1. **Secure** — rotate the admin password, create non-admin
 *      operators, link external identity if needed.
 *   2. **Reach** — DNS / hostnames so the operator can hit Jellyfin
 *      from a TV on the LAN, or remotely via Cloudflare.
 *   3. **Watch** — *arr is fetching content already, click into
 *      Jellyfin to start playing it. The shortest path from
 *      "system is up" to "I'm watching something."
 *
 * The cards link to the relevant page rather than embedding the
 * action surface, so the dashboard's first impression stays light.
 * Operators who want to dig deeper follow the link; everyone else
 * sees the system "just works" without a setup wall.
 */
export function QuickStartCards() {
  return (
    <Card data-testid="quick-start-cards">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Rocket aria-hidden className="size-4" />
          Quick start
        </CardTitle>
        <CardDescription>
          The shortest path from a fresh install to a working media
          stack. Each step is optional — the system runs with sane
          defaults out of the box.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid gap-3 md:grid-cols-3">
          <QuickStep
            icon={KeyRound}
            num={1}
            title="Secure your stack"
            primary={{ label: "Open /me", to: "/me" }}
            secondary={{ label: "User management", to: "/users" }}
          >
            Rotate the seed admin password, then create a non-admin
            user for each household member. The seed credential
            stops working as soon as you rotate.
          </QuickStep>
          <QuickStep
            icon={Globe2}
            num={2}
            title="Set DNS / hostnames"
            primary={{ label: "Open /routing", to: "/routing" }}
            external={{
              label: "DNS guide",
              href:
                "https://github.com/mploschiavo/mediaserver/blob/main/docs/runbooks/dns.md",
            }}
          >
            On the LAN, point ``apps.media-stack.local`` at this
            host's IP via your router's DNS or each device's
            hosts file. For remote access, set up Cloudflare DNS
            and Tunnel — see the guide for step-by-step.
          </QuickStep>
          <QuickStep
            icon={PlayCircle}
            num={3}
            title="Watch something"
            primary={{ label: "Open /content", to: "/content" }}
            external={{
              label: "Jellyfin",
              href: "/app/jellyfin/",
            }}
          >
            *arr is already grabbing releases — your library will
            populate within minutes. Open Jellyfin in any browser
            (or the iOS / Android / Roku / Apple TV app) and start
            playing.
          </QuickStep>
        </div>
        <div className="mt-3 flex items-center gap-1.5 text-[11px] text-fg-faint">
          <Users aria-hidden className="size-3" />
          <span>
            Need something else? Every other action lives in its own
            page — explore via the sidebar. None of it is required
            to get started.
          </span>
        </div>
      </CardContent>
    </Card>
  );
}

interface StepLink {
  label: string;
  to: string;
}

interface ExternalStepLink {
  label: string;
  href: string;
}

function QuickStep({
  icon: Icon,
  num,
  title,
  primary,
  secondary,
  external,
  children,
}: {
  icon: React.ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
  num: number;
  title: string;
  primary: StepLink;
  secondary?: StepLink;
  external?: ExternalStepLink;
  children: React.ReactNode;
}) {
  return (
    <div
      className="flex flex-col gap-2 rounded-md border border-border bg-bg-1 p-3"
      data-testid={`quick-start-step-${num}`}
    >
      <div className="flex items-center gap-2 text-sm font-medium text-fg">
        <span
          aria-hidden
          className="flex size-5 items-center justify-center rounded-full bg-info/15 text-[11px] font-mono text-info"
        >
          {num}
        </span>
        <Icon aria-hidden className="size-3.5 text-fg-muted" />
        {title}
      </div>
      <p className="text-xs text-fg-muted">{children}</p>
      <div className="mt-auto flex flex-wrap gap-1.5 pt-1">
        <Button asChild size="sm" variant="primary">
          <Link to={primary.to}>{primary.label}</Link>
        </Button>
        {secondary ? (
          <Button asChild size="sm" variant="ghost">
            <Link to={secondary.to}>{secondary.label}</Link>
          </Button>
        ) : null}
        {external ? (
          <Button asChild size="sm" variant="ghost">
            <a
              href={external.href}
              target="_blank"
              rel="noreferrer noopener"
            >
              {external.label} ↗
            </a>
          </Button>
        ) : null}
      </div>
    </div>
  );
}
