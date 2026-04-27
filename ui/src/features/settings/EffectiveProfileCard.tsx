import { useMemo, useRef, type ReactNode } from "react";
import { ChevronDown, FileText } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { ApiErrorTile } from "@/components/ApiErrorTile";
import { asArray, asObjectMap } from "@/lib/coerce";
import { useProfileYaml, type ProfileResponse } from "./hooks";

// ---------------------------------------------------------------------------
// Tiny YAML extractor.
//
// The bundle budget (191/200 KB main) doesn't have room for js-yaml, and
// the controller's profile.yaml shape is shallow + indentation-sensitive
// in a predictable way. This extractor walks the document line-by-line
// and pulls out only the fields the EffectiveProfileCard renders. It
// supports:
//   - top-level scalar fields (`gateway_host: foo`)
//   - nested sections one level deep (`network:` → indented children)
//   - list items under `routing.direct_hosts` (`- foo`)
//   - subkeys under `media-server` and `iptv` blocks
//
// Anything fancier (anchors, multi-doc, flow style) falls through and
// renders as "—". The editable textarea below stays the canonical
// edit path; this card is purely a glanceable summary.
// ---------------------------------------------------------------------------

interface ExtractedProfile {
  network: {
    gateway_host?: string;
    base_domain?: string;
    strategy?: string;
  };
  services: readonly string[];
  auth: {
    mode?: string;
    oidc_enabled?: boolean;
  };
  routing: {
    direct_hosts: readonly string[];
  };
  mediaServer: {
    api_url?: string;
    has_key: boolean;
  };
  iptv: {
    tuner?: string;
    guide?: string;
  };
}

function emptyProfile(): ExtractedProfile {
  return {
    network: {},
    services: [],
    auth: {},
    routing: { direct_hosts: [] },
    mediaServer: { has_key: false },
    iptv: {},
  };
}

function stripQuotes(v: string): string {
  const trimmed = v.trim();
  if (trimmed.length === 0) return "";
  if (
    (trimmed.startsWith('"') && trimmed.endsWith('"')) ||
    (trimmed.startsWith("'") && trimmed.endsWith("'"))
  ) {
    return trimmed.slice(1, -1);
  }
  return trimmed;
}

/**
 * Read a top-level mapping key's child block. Returns `[indent, lines]`
 * where `lines` is the contiguous block whose indentation is >= the
 * key's indent + 2. Stops at the next sibling or document end.
 */
function readBlock(
  lines: readonly string[],
  startIdx: number,
): { indent: number; rows: string[] } {
  const out: string[] = [];
  let inferIndent: number | null = null;
  for (let i = startIdx; i < lines.length; i++) {
    const line = lines[i];
    if (line === undefined) break;
    if (/^\s*$/.test(line) || /^\s*#/.test(line)) {
      out.push(line);
      continue;
    }
    const m = /^(\s*)/.exec(line);
    const ind = m?.[1]?.length ?? 0;
    if (inferIndent === null) {
      inferIndent = ind;
    }
    if (ind < inferIndent) break;
    out.push(line);
  }
  return { indent: inferIndent ?? 0, rows: out };
}

function parseBool(value: string | undefined): boolean | undefined {
  if (value === undefined) return undefined;
  const v = value.toLowerCase();
  if (v === "true" || v === "yes" || v === "on") return true;
  if (v === "false" || v === "no" || v === "off") return false;
  return undefined;
}

/**
 * Parse a flat mapping block into a key→string-value record. Skips
 * nested blocks (their entries appear with greater indentation than
 * the block's own indent + 2).
 */
function parseFlatMap(rows: readonly string[]): Record<string, string> {
  const result: Record<string, string> = {};
  let baseIndent: number | null = null;
  for (const line of rows) {
    if (/^\s*$/.test(line) || /^\s*#/.test(line)) continue;
    const m = /^(\s*)([\w.-]+)\s*:(.*)$/.exec(line);
    if (!m) continue;
    const indent = m[1]!.length;
    if (baseIndent === null) baseIndent = indent;
    if (indent !== baseIndent) continue;
    const key = m[2]!;
    const rest = m[3]!.trim();
    if (rest === "") continue;
    result[key] = stripQuotes(rest);
  }
  return result;
}

/**
 * Parse a `- value` list (one item per line) at the smallest
 * indentation. Returns the values verbatim (quotes stripped).
 */
function parseList(rows: readonly string[]): string[] {
  const out: string[] = [];
  let baseIndent: number | null = null;
  for (const line of rows) {
    if (/^\s*$/.test(line) || /^\s*#/.test(line)) continue;
    const m = /^(\s*)-\s+(.*)$/.exec(line);
    if (!m) continue;
    const indent = m[1]!.length;
    if (baseIndent === null) baseIndent = indent;
    if (indent !== baseIndent) continue;
    out.push(stripQuotes(m[2]!));
  }
  return out;
}

/**
 * Pull a nested key's block (e.g. `routing.direct_hosts`) out of the
 * larger block. Returns the rows under that subkey.
 */
function findChildBlock(
  rows: readonly string[],
  key: string,
): readonly string[] {
  const re = new RegExp(`^(\\s*)${key}\\s*:\\s*$`);
  for (let i = 0; i < rows.length; i++) {
    const line = rows[i]!;
    const m = re.exec(line);
    if (m) {
      const baseIndent = m[1]!.length;
      const block: string[] = [];
      for (let j = i + 1; j < rows.length; j++) {
        const next = rows[j]!;
        if (/^\s*$/.test(next) || /^\s*#/.test(next)) {
          block.push(next);
          continue;
        }
        const im = /^(\s*)/.exec(next);
        const ind = im ? im[1]!.length : 0;
        if (ind <= baseIndent) break;
        block.push(next);
      }
      return block;
    }
  }
  return [];
}

/**
 * Find a top-level block in the YAML (e.g. `network:`) and return its
 * rows. Top-level keys are at indent 0.
 */
function findTopBlock(
  lines: readonly string[],
  key: string,
): { value?: string; rows: readonly string[] } {
  const re = new RegExp(`^${key}\\s*:(.*)$`);
  for (let i = 0; i < lines.length; i++) {
    const m = re.exec(lines[i]!);
    if (m) {
      const inline = m[1]!.trim();
      if (inline !== "") return { value: stripQuotes(inline), rows: [] };
      const block = readBlock(lines, i + 1);
      return { rows: block.rows };
    }
  }
  return { rows: [] };
}

export function extractProfile(yaml: string): ExtractedProfile {
  const out = emptyProfile();
  if (!yaml) return out;
  const lines = yaml.split("\n");

  // network: gateway_host / base_domain / strategy
  const network = findTopBlock(lines, "network");
  const nMap = parseFlatMap(network.rows);
  if (nMap.gateway_host) out.network.gateway_host = nMap.gateway_host;
  if (nMap.base_domain) out.network.base_domain = nMap.base_domain;
  if (nMap.strategy) out.network.strategy = nMap.strategy;

  // services: a flat block of `name: enabled`-ish entries OR a list
  const services = findTopBlock(lines, "services");
  const sList = parseList(services.rows);
  if (sList.length > 0) {
    out.services = sList;
  } else {
    const sMap = parseFlatMap(services.rows);
    out.services = Object.keys(sMap);
  }

  // auth: { mode, oidc: { enabled } }
  const auth = findTopBlock(lines, "auth");
  const aMap = parseFlatMap(auth.rows);
  if (aMap.mode) out.auth.mode = aMap.mode;
  const oidcRows = findChildBlock(auth.rows, "oidc");
  const oMap = parseFlatMap(oidcRows);
  const enabled = parseBool(oMap.enabled);
  if (enabled !== undefined) out.auth.oidc_enabled = enabled;

  // routing: { direct_hosts: [ ... ] }
  const routing = findTopBlock(lines, "routing");
  const directRows = findChildBlock(routing.rows, "direct_hosts");
  out.routing.direct_hosts = parseList(directRows);

  // media-server: { api_url, api_key (presence only) }
  const ms = findTopBlock(lines, "media-server");
  const mMap = parseFlatMap(ms.rows);
  if (mMap.api_url) out.mediaServer.api_url = mMap.api_url;
  // The card NEVER renders the key — derive a presence boolean only.
  out.mediaServer.has_key = Boolean(
    mMap.api_key && mMap.api_key.length > 0 && mMap.api_key !== "null",
  );

  // iptv: { tuner, guide }
  const iptv = findTopBlock(lines, "iptv");
  const iMap = parseFlatMap(iptv.rows);
  if (iMap.tuner) out.iptv.tuner = iMap.tuner;
  if (iMap.guide) out.iptv.guide = iMap.guide;
  // Newer profiles may use `tuner_url` / `guide_url`; accept both.
  if (!out.iptv.tuner && iMap.tuner_url) out.iptv.tuner = iMap.tuner_url;
  if (!out.iptv.guide && iMap.guide_url) out.iptv.guide = iMap.guide_url;

  return out;
}

function readYaml(p: ProfileResponse | undefined): string {
  if (!p) return "";
  if (typeof p.yaml === "string") return p.yaml;
  if (typeof p.content === "string") return p.content;
  return "";
}

// ---------------------------------------------------------------------------
// Section UI primitives.
// ---------------------------------------------------------------------------

interface SectionProps {
  id: string;
  title: string;
  /** Defaults open; collapsed when defaultOpen=false. */
  defaultOpen?: boolean;
  empty?: boolean;
  children: ReactNode;
}

function Section({
  id,
  title,
  defaultOpen = true,
  empty = false,
  children,
}: SectionProps) {
  // `<details>` defaults: we use a ref to set the initial `open`
  // state exactly once on first mount, then leave subsequent
  // rendering uncontrolled so an operator's toggle persists across
  // YAML edits. React's `open` prop on `<details>` reflects to the
  // DOM on every render, which would otherwise undo a manual toggle
  // when the parent re-renders.
  const initialised = useRef(false);
  const refCallback = (el: HTMLDetailsElement | null) => {
    if (el && !initialised.current) {
      el.open = defaultOpen;
      initialised.current = true;
    }
  };
  return (
    <details
      ref={refCallback}
      data-testid={`profile-section-${id}`}
      className="group rounded-md border border-border bg-bg-1"
    >
      <summary className="flex cursor-pointer list-none items-center justify-between gap-2 rounded-md px-3 py-2 text-sm font-medium text-fg outline-none transition-colors hover:bg-bg-2 focus-visible:ring-2 focus-visible:ring-ring">
        <span>{title}</span>
        <span className="flex items-center gap-2">
          {empty ? (
            <span className="text-xs font-normal text-fg-muted">
              not configured
            </span>
          ) : null}
          <ChevronDown
            aria-hidden
            className="size-4 text-fg-muted transition-transform group-open:rotate-180"
          />
        </span>
      </summary>
      <div className="border-t border-border px-3 py-3 text-sm">{children}</div>
      {/* TODO(controller): surface profile.yaml line numbers per
          field so the footnote can read "Source: profile.yaml @
          <line>". For now we only annotate the section. */}
      <div
        className="border-t border-border bg-bg px-3 py-1.5 text-[11px] text-fg-muted"
        data-testid={`profile-section-source-${id}`}
      >
        Source: profile.yaml{" "}
        <span className="text-fg-muted/70">
          (line numbers pending — see TODO)
        </span>
      </div>
    </details>
  );
}

interface FieldProps {
  label: string;
  value?: string | boolean | null;
  testid?: string;
  /** When true, render a Yes/No badge instead of a string. */
  asBoolean?: boolean;
}

function Field({ label, value, testid, asBoolean }: FieldProps) {
  const isMissing =
    value === undefined ||
    value === null ||
    (typeof value === "string" && value.trim() === "");

  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2 py-1.5 text-sm">
      <span className="text-fg-muted">{label}</span>
      <span className="font-mono text-xs" data-testid={testid}>
        {isMissing ? (
          <span className="text-fg-muted">—</span>
        ) : asBoolean ? (
          <Badge variant={value ? "success" : "outline"}>
            {value ? "yes" : "no"}
          </Badge>
        ) : (
          String(value)
        )}
      </span>
    </div>
  );
}

/**
 * Read-only "Effective profile" panel. Renders the bootstrap
 * profile sourced from `/api/profile` (same hook as the editor) as
 * collapsible sections. The editable textarea remains the canonical
 * edit path; this card just gives the operator a glanceable summary
 * without forcing them to read the YAML.
 */
export function EffectiveProfileCard() {
  const profile = useProfileYaml();
  const yaml = readYaml(profile.data);
  const extracted = useMemo(() => extractProfile(yaml), [yaml]);

  return (
    <Card data-testid="effective-profile-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FileText className="size-4" aria-hidden />
          Effective profile
        </CardTitle>
        <CardDescription>
          Read-only summary of the running bootstrap profile. Use the
          editor below to change values.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {profile.isLoading ? (
          <div
            data-testid="effective-profile-loading"
            className="flex flex-col gap-2"
          >
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : profile.error ? (
          <ApiErrorTile
            error={profile.error}
            onRetry={() => void profile.refetch()}
          />
        ) : !yaml ? (
          <p
            className="text-sm text-fg-muted"
            data-testid="effective-profile-empty"
          >
            No profile loaded.
          </p>
        ) : (
          <>
            <Section
              id="network"
              title="Network"
              empty={
                !extracted.network.gateway_host &&
                !extracted.network.base_domain &&
                !extracted.network.strategy
              }
            >
              <Field
                label="gateway_host"
                value={extracted.network.gateway_host}
                testid="profile-network-gateway-host"
              />
              <Field
                label="base_domain"
                value={extracted.network.base_domain}
                testid="profile-network-base-domain"
              />
              <Field
                label="strategy"
                value={extracted.network.strategy}
                testid="profile-network-strategy"
              />
            </Section>

            <Section
              id="services"
              title="Services"
              empty={extracted.services.length === 0}
            >
              {extracted.services.length === 0 ? (
                <p className="text-sm text-fg-muted">
                  No services declared.
                </p>
              ) : (
                <ul
                  className="flex flex-wrap gap-1.5"
                  data-testid="profile-services-list"
                >
                  {asArray(extracted.services).map((s) => (
                    <li key={s}>
                      <Badge variant="outline">{s}</Badge>
                    </li>
                  ))}
                </ul>
              )}
            </Section>

            <Section
              id="auth"
              title="Auth"
              empty={
                !extracted.auth.mode &&
                extracted.auth.oidc_enabled === undefined
              }
            >
              <Field
                label="mode"
                value={extracted.auth.mode}
                testid="profile-auth-mode"
              />
              <Field
                label="oidc enabled"
                value={extracted.auth.oidc_enabled ?? null}
                asBoolean
                testid="profile-auth-oidc"
              />
            </Section>

            <Section
              id="routing"
              title="Routing → direct_hosts"
              defaultOpen={false}
              empty={extracted.routing.direct_hosts.length === 0}
            >
              {extracted.routing.direct_hosts.length === 0 ? (
                <p className="text-sm text-fg-muted">No direct hosts.</p>
              ) : (
                <ul
                  className="flex flex-col gap-1 text-xs"
                  data-testid="profile-routing-direct-hosts"
                >
                  {asArray(extracted.routing.direct_hosts).map((h) => (
                    <li
                      key={h}
                      className="rounded border border-border bg-bg px-2 py-1 font-mono"
                    >
                      {h}
                    </li>
                  ))}
                </ul>
              )}
            </Section>

            <Section
              id="media-server"
              title="Media server"
              empty={
                !extracted.mediaServer.api_url &&
                !extracted.mediaServer.has_key
              }
            >
              <Field
                label="api_url"
                value={extracted.mediaServer.api_url}
                testid="profile-media-server-api-url"
              />
              <Field
                label="api_key set"
                value={extracted.mediaServer.has_key}
                asBoolean
                testid="profile-media-server-has-key"
              />
            </Section>

            <Section
              id="iptv"
              title="IPTV"
              defaultOpen={false}
              empty={!extracted.iptv.tuner && !extracted.iptv.guide}
            >
              <Field
                label="tuner"
                value={extracted.iptv.tuner}
                testid="profile-iptv-tuner"
              />
              <Field
                label="guide"
                value={extracted.iptv.guide}
                testid="profile-iptv-guide"
              />
            </Section>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// Defensive helper kept exported so future surfaces (drift overlays,
// onboarding) can reuse the live-shape coercion without re-parsing.
export function readProfileYaml(p: ProfileResponse | undefined): string {
  // Re-export under a name that matches the rest of the feature.
  // (The internal `readYaml` stays private; some consumers walk the
  // hook directly, and `asObjectMap` lets us be defensive about a
  // future shape that emits the YAML under a wrapper key.)
  if (!p) return "";
  if (typeof p.yaml === "string") return p.yaml;
  if (typeof p.content === "string") return p.content;
  const map = asObjectMap(p);
  if (typeof map.profile === "string") return map.profile;
  return "";
}
