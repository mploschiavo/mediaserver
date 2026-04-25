import { useCallback, useEffect, useMemo, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { ChevronDown, ChevronRight, Folder } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";
import { asArray } from "@/lib/coerce";
import type {
  JobHistoryEntry,
  JobHistoryJobResult,
  JobMeta,
  JobTreeNode,
} from "./hooks";

interface JobsTreeViewProps {
  tree: readonly JobTreeNode[];
  /** Flat catalog used to look up service/label metadata for each node. */
  catalog: ReadonlyMap<string, JobMeta>;
  /** Most-recent history entry — drives the per-node status dot. */
  latest?: JobHistoryEntry;
  /** Currently-selected job name (null when nothing's selected). */
  selectedName: string | null;
  onSelect: (name: string, meta: JobMeta | undefined) => void;
  /**
   * External "expand to this name" signal. When the value changes, the
   * tree expands every ancestor of the named node. Useful for the
   * detail panel's "Show in tree" buttons.
   */
  revealName?: string | null;
  /**
   * Name of a job currently being run via `useRunAction`. The matching
   * leaf paints a "running…" pill so the operator can locate the
   * affected node while the mutation is in flight.
   */
  inFlightName?: string | null;
}

type StatusKind = "ok" | "skipped" | "error" | "none";

function statusFor(
  name: string,
  latest: JobHistoryEntry | undefined,
): StatusKind {
  const result = latest?.jobs?.[name];
  const raw = result?.status;
  if (raw === "ok") return "ok";
  if (raw === "skipped") return "skipped";
  if (raw === "error" || raw === "errors" || raw === "failed") return "error";
  return "none";
}

const STATUS_TONE: Record<StatusKind, string> = {
  ok: "bg-success",
  skipped: "bg-warning",
  error: "bg-danger",
  // Faint dot keeps the row alignment stable for jobs that have no
  // result in the latest batch (most of them).
  none: "bg-border-strong",
};

const STATUS_LABEL: Record<StatusKind, string> = {
  ok: "ran successfully",
  skipped: "skipped (deps unmet)",
  error: "errored",
  none: "no recent run",
};

/**
 * Pull the truncated error string for a leaf, when the latest batch
 * recorded one. Returns the full string + a 1-line truncation
 * (≤80 chars) so the row can show a hover tooltip with the full text.
 */
function errorSnippet(
  name: string,
  latest: JobHistoryEntry | undefined,
): { full: string; short: string } | null {
  const result = latest?.jobs?.[name] as JobHistoryJobResult | undefined;
  const raw = result?.error;
  if (typeof raw !== "string" || raw.length === 0) return null;
  const oneLine = raw.replace(/\s+/g, " ").trim();
  const short = oneLine.length > 80 ? `${oneLine.slice(0, 79)}…` : oneLine;
  return { full: oneLine, short };
}

/**
 * Build a name -> ancestor-set lookup so revealing a leaf can expand
 * every parent in one pass. Walks the tree depth-first; the cost is
 * O(nodes) which is well below the controller's catalog size.
 */
function buildAncestors(
  tree: readonly JobTreeNode[],
): Map<string, readonly string[]> {
  const out = new Map<string, readonly string[]>();
  const walk = (node: JobTreeNode, trail: readonly string[]) => {
    out.set(node.name, trail);
    const next = [...trail, node.name];
    for (const child of asArray<JobTreeNode>(node.sub_jobs)) {
      walk(child, next);
    }
  };
  for (const root of tree) walk(root, []);
  return out;
}

/**
 * Recursive tree renderer. Each row has:
 *   - chevron (▶/▼) when there are children, or a folder dot for leaves,
 *   - the job name + service badge,
 *   - a status dot reflecting the latest batch's outcome for that name.
 *
 * Click the name → `onSelect`. Click the chevron → expand/collapse.
 * Top-level nodes start expanded; deeper levels start collapsed so the
 * tree opens "spec mode" rather than a hundred-line dump.
 */
export function JobsTreeView({
  tree,
  catalog,
  latest,
  selectedName,
  onSelect,
  revealName,
  inFlightName,
}: JobsTreeViewProps) {
  const ancestors = useMemo(() => buildAncestors(tree), [tree]);

  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => {
    const init = new Set<string>();
    for (const node of tree) init.add(node.name);
    return init;
  });

  // Reveal-to-name: when the prop changes, expand every ancestor of
  // the requested node. We commit through `setExpanded` inside an
  // effect so the render pass stays pure. Idempotent: if every
  // ancestor is already open we leave the set untouched.
  useEffect(() => {
    if (!revealName) return;
    const trail = ancestors.get(revealName);
    if (!trail || trail.length === 0) return;
    setExpanded((prev) => {
      let next = prev;
      let mutated = false;
      for (const ancestor of trail) {
        if (!next.has(ancestor)) {
          if (!mutated) {
            next = new Set(prev);
            mutated = true;
          }
          (next as Set<string>).add(ancestor);
        }
      }
      return mutated ? next : prev;
    });
  }, [revealName, ancestors]);

  // Same idea for `inFlightName`: keep the running leaf visible.
  useEffect(() => {
    if (!inFlightName) return;
    const trail = ancestors.get(inFlightName);
    if (!trail || trail.length === 0) return;
    setExpanded((prev) => {
      let next = prev;
      let mutated = false;
      for (const ancestor of trail) {
        if (!next.has(ancestor)) {
          if (!mutated) {
            next = new Set(prev);
            mutated = true;
          }
          (next as Set<string>).add(ancestor);
        }
      }
      return mutated ? next : prev;
    });
  }, [inFlightName, ancestors]);

  const toggle = useCallback((name: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }, []);

  if (tree.length === 0) {
    return (
      <div
        className="rounded-lg border border-dashed border-border bg-bg-1/40 px-4 py-8 text-center text-sm text-fg-muted"
        data-testid="jobs-tree-empty"
      >
        Tree hierarchy is empty.
      </div>
    );
  }

  return (
    <div
      className="flex flex-col gap-0.5 text-sm"
      role="tree"
      aria-label="Job hierarchy"
      data-testid="jobs-tree"
    >
      {tree.map((node) => (
        <TreeNode
          key={node.name}
          node={node}
          depth={0}
          expanded={expanded}
          toggle={toggle}
          onSelect={onSelect}
          selectedName={selectedName}
          catalog={catalog}
          latest={latest}
          inFlightName={inFlightName ?? null}
        />
      ))}
    </div>
  );
}

interface TreeNodeProps {
  node: JobTreeNode;
  depth: number;
  expanded: ReadonlySet<string>;
  toggle: (name: string) => void;
  onSelect: (name: string, meta: JobMeta | undefined) => void;
  selectedName: string | null;
  catalog: ReadonlyMap<string, JobMeta>;
  latest: JobHistoryEntry | undefined;
  inFlightName: string | null;
}

function TreeNode({
  node,
  depth,
  expanded,
  toggle,
  onSelect,
  selectedName,
  catalog,
  latest,
  inFlightName,
}: TreeNodeProps) {
  const reduce = useReducedMotion();
  const children = asArray<JobTreeNode>(node.sub_jobs);
  const hasChildren = children.length > 0;
  const isOpen = expanded.has(node.name);
  const isSelected = selectedName === node.name;
  const meta = catalog.get(node.name);
  const status = statusFor(node.name, latest);
  const errSnip = status === "error" ? errorSnippet(node.name, latest) : null;
  const isRunning = inFlightName === node.name;
  // Only show the dim service tag on leaves — interior nodes are
  // category headers and don't carry a meaningful service slug.
  const showLeafService = !hasChildren && meta?.service;

  return (
    <div role="treeitem" aria-expanded={hasChildren ? isOpen : undefined}>
      <div
        className={cn(
          "group flex flex-wrap items-center gap-2 rounded-md px-2 py-1.5 transition-colors",
          isSelected
            ? "bg-[color-mix(in_oklab,var(--color-accent)_15%,transparent)] text-fg"
            : "[@media(hover:hover)]:hover:bg-bg-2",
        )}
        style={{ paddingLeft: `${0.5 + depth * 0.85}rem` }}
        data-testid={`jobs-tree-row-${node.name}`}
      >
        {hasChildren ? (
          <button
            type="button"
            onClick={() => toggle(node.name)}
            className="flex size-5 shrink-0 items-center justify-center rounded text-fg-muted [@media(hover:hover)]:hover:text-fg"
            aria-label={isOpen ? "Collapse" : "Expand"}
            data-testid={`jobs-tree-chevron-${node.name}`}
          >
            {isOpen ? (
              <ChevronDown className="size-3.5" aria-hidden />
            ) : (
              <ChevronRight className="size-3.5" aria-hidden />
            )}
          </button>
        ) : (
          <span
            className="flex size-5 shrink-0 items-center justify-center text-fg-faint"
            aria-hidden
          >
            <Folder className="size-3" />
          </span>
        )}

        <button
          type="button"
          onClick={() => onSelect(node.name, meta)}
          className="flex min-w-0 flex-1 items-center gap-2 truncate text-left font-medium text-fg"
          data-testid={`jobs-tree-name-${node.name}`}
        >
          <span
            className={cn("size-2 shrink-0 rounded-full", STATUS_TONE[status])}
            aria-label={STATUS_LABEL[status]}
            title={STATUS_LABEL[status]}
            data-testid={`jobs-tree-dot-${node.name}`}
            data-status={status}
          />
          <span className="truncate">{meta?.label ?? node.name}</span>
          {showLeafService ? (
            <span
              className="shrink-0 truncate text-[10px] uppercase tracking-wide text-fg-faint"
              data-testid={`jobs-tree-service-${node.name}`}
              title={`Service: ${meta?.service}`}
            >
              {meta?.service}
            </span>
          ) : null}
          {meta?.service && hasChildren ? (
            <Badge
              variant="outline"
              className="shrink-0 px-1.5 py-0 text-[10px] uppercase tracking-wide"
            >
              {meta.service}
            </Badge>
          ) : null}
        </button>
        {isRunning ? (
          <motion.span
            className="ml-1 inline-flex shrink-0 items-center gap-1 rounded-full border border-accent/40 bg-accent/10 px-1.5 py-0 text-[10px] font-medium uppercase tracking-wide text-accent"
            animate={
              reduce ? undefined : { opacity: [0.5, 1, 0.5] }
            }
            transition={
              reduce
                ? undefined
                : { duration: 1.2, repeat: Infinity, ease: "easeInOut" }
            }
            data-testid={`jobs-tree-running-${node.name}`}
          >
            <span className="size-1.5 rounded-full bg-accent" aria-hidden />
            running…
          </motion.span>
        ) : null}
        {status === "error" && errSnip ? (
          <span
            className="ml-1 inline-block min-w-0 max-w-[18ch] truncate text-xs text-danger"
            title={errSnip.full}
            data-testid={`jobs-tree-error-${node.name}`}
          >
            {errSnip.short}
          </span>
        ) : null}
        {status === "skipped" ? (
          <span
            className="ml-1 inline-block text-xs text-warning"
            title="Required dependency not satisfied; click to see deps"
            data-testid={`jobs-tree-skipped-${node.name}`}
          >
            (skipped)
          </span>
        ) : null}
      </div>

      {hasChildren && isOpen ? (
        <div role="group">
          {children.map((child) => (
            <TreeNode
              key={child.name}
              node={child}
              depth={depth + 1}
              expanded={expanded}
              toggle={toggle}
              onSelect={onSelect}
              selectedName={selectedName}
              catalog={catalog}
              latest={latest}
              inFlightName={inFlightName}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}
