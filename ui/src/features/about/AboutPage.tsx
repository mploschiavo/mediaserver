import {
  ExternalLink,
  Github,
  Heart,
  Info,
  RefreshCw,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { fetcher } from "@/api/client";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

// Live response shape of /api/stack/update — see
// ``stack_update.check_for_update``. The shared
// ``useStackUpdate`` hook claims a different (aspirational) shape
// for back-compat reasons; reading the raw keys here keeps the
// drift banner's existing nullish behavior unchanged.
interface RawStackUpdate {
  current?: string;
  latest?: string;
  upgradable?: boolean;
  release_url?: string;
}

function useAboutVersionInfo() {
  return useQuery<RawStackUpdate>({
    queryKey: ["about", "stack-update"],
    queryFn: () => fetcher<RawStackUpdate>("api/stack/update"),
    staleTime: 60_000,
    retry: false,
  });
}

const GITHUB_URL = "https://github.com/mploschiavo/mediaserver";
const PAYPAL_URL =
  "https://www.paypal.com/donate?hosted_button_id=XKDG7XXVEQK3W";
const LICENSE = "AGPL-3.0";

/**
 * /about — version + provenance surface. Shows the running controller
 * version (from /api/stack/update.current_version), the SPA build
 * version (baked in by Vite from ui/package.json), the upstream
 * release available (when newer than the running controller), plus
 * source/license/donate links.
 *
 * Operators paste this into bug reports; users link it from
 * support threads. Nothing here is reactive to live state apart from
 * the upstream-release probe.
 */
export function AboutPage(): JSX.Element {
  const update = useAboutVersionInfo();
  const buildVersion = (
    import.meta.env.VITE_BUILD_VERSION ?? ""
  ).trim();
  const controllerVersion = (update.data?.current ?? "").trim();
  const latestVersion = (update.data?.latest ?? "").trim();
  const upstreamAvailable =
    Boolean(update.data?.upgradable) &&
    latestVersion &&
    latestVersion !== controllerVersion;

  return (
    <div
      className="mx-auto flex w-full max-w-4xl flex-col gap-6 p-4 sm:p-6"
      data-testid="about-page"
    >
      <Card data-testid="about-versions-card">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Info aria-hidden className="size-4 text-fg-muted" />
            Version
          </CardTitle>
          <CardDescription>
            What's running in this deployment. Include these numbers
            when filing a bug.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <VersionRow
            label="UI (this dashboard)"
            value={buildVersion || "dev"}
            testId="about-ui-version"
          />
          <VersionRow
            label="Controller"
            value={
              update.isLoading
                ? "loading…"
                : controllerVersion || "unavailable"
            }
            testId="about-controller-version"
          />
          {upstreamAvailable ? (
            <div
              className="flex flex-wrap items-center gap-2 rounded-md border border-[color-mix(in_oklab,var(--color-info)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-info)_8%,transparent)] p-3 text-sm"
              data-testid="about-upgrade-hint"
            >
              <RefreshCw aria-hidden className="size-4 text-info" />
              <span className="text-fg">
                A newer release is available:{" "}
                <span className="font-mono font-medium">
                  {latestVersion}
                </span>
              </span>
              <a
                href={GITHUB_URL + "/releases"}
                target="_blank"
                rel="noopener noreferrer"
                className="ml-auto text-info hover:underline"
              >
                Release notes
              </a>
            </div>
          ) : null}
          {update.error ? (
            <p
              role="alert"
              className="text-xs text-danger"
              data-testid="about-versions-error"
            >
              Could not contact the controller for its running
              version. The UI build version above is still accurate.
            </p>
          ) : null}
        </CardContent>
      </Card>

      <Card data-testid="about-project-card">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Github aria-hidden className="size-4 text-fg-muted" />
            Project
          </CardTitle>
          <CardDescription>
            Source code, license, and ways to support the project.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <Button
              asChild
              variant="outline"
              size="sm"
              data-testid="about-github-link"
            >
              <a
                href={GITHUB_URL}
                target="_blank"
                rel="noopener noreferrer"
              >
                <Github aria-hidden className="size-4" />
                GitHub
                <ExternalLink aria-hidden className="size-3" />
              </a>
            </Button>
            <Button
              asChild
              variant="outline"
              size="sm"
              data-testid="about-donate-link"
            >
              <a
                href={PAYPAL_URL}
                target="_blank"
                rel="noopener noreferrer"
              >
                <Heart aria-hidden className="size-4 text-danger" />
                Donate via PayPal
                <ExternalLink aria-hidden className="size-3" />
              </a>
            </Button>
            <Button
              asChild
              variant="ghost"
              size="sm"
              data-testid="about-license-link"
            >
              <a
                href={`${GITHUB_URL}/blob/main/LICENSE`}
                target="_blank"
                rel="noopener noreferrer"
              >
                License: {LICENSE}
                <ExternalLink aria-hidden className="size-3" />
              </a>
            </Button>
          </div>
          <p className="text-xs text-fg-muted">
            Self-hosted media-automation stack — Sonarr/Radarr/
            Jellyfin/Bazarr/Prowlarr orchestration with a single
            operator dashboard. Donations are optional and gratefully
            received; they keep development time funded.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

function VersionRow({
  label,
  value,
  testId,
}: {
  label: string;
  value: string;
  testId: string;
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border border-border bg-bg-1 p-3">
      <span className="text-sm text-fg">{label}</span>
      <span
        className="font-mono text-sm tabular-nums text-fg"
        data-testid={testId}
      >
        {value}
      </span>
    </div>
  );
}
