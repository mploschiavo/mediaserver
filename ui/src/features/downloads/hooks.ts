// Feature-local hooks for the Downloads surface.

import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { fetcher } from "@/api/client";

// ---- Active downloads -----------------------------------------------------

export interface DownloadItem {
  name?: string;
  progress?: number;
  state?: string;
  size?: number;
  dlspeed?: number;
  [key: string]: unknown;
}

export interface DownloadClientBlock {
  active?: number;
  speed?: string;
  items?: readonly DownloadItem[];
  error?: string;
}

export interface DownloadsResponse {
  qbittorrent?: DownloadClientBlock;
  sabnzbd?: DownloadClientBlock;
  [key: string]: unknown;
}

const DOWNLOADS_KEY = ["downloads", "active"] as const;
const HISTORY_KEY = ["downloads", "history"] as const;
const ANALYTICS_KEY = ["downloads", "analytics"] as const;
const CATEGORIES_KEY = ["downloads", "categories"] as const;

export function useDownloads(): UseQueryResult<DownloadsResponse> {
  return useQuery({
    queryKey: DOWNLOADS_KEY,
    queryFn: () => fetcher<DownloadsResponse>("api/downloads"),
    refetchInterval: 5_000,
  });
}

// ---- History --------------------------------------------------------------

export interface HistoryEntry {
  title?: string;
  event?: string;
  date?: string;
  [key: string]: unknown;
}

export interface DownloadHistoryResponse {
  history?: Record<string, readonly HistoryEntry[]>;
}

export function useDownloadHistory(): UseQueryResult<DownloadHistoryResponse> {
  return useQuery({
    queryKey: HISTORY_KEY,
    queryFn: () =>
      fetcher<DownloadHistoryResponse>("api/download-history"),
    staleTime: 60_000,
  });
}

// ---- Analytics ------------------------------------------------------------

/**
 * /api/download-analytics is loose. The known fields below cover the
 * controller's current shape; everything else passes through.
 */
export interface DownloadAnalyticsResponse {
  totals?: {
    completed?: number;
    failed?: number;
    grabbed?: number;
    [key: string]: unknown;
  };
  /** Time series — `[{ts, count}, ...]`. */
  series?: readonly { ts?: string; count?: number; [key: string]: unknown }[];
  by_service?: Record<string, { completed?: number; failed?: number }>;
  [key: string]: unknown;
}

export function useDownloadAnalytics(): UseQueryResult<DownloadAnalyticsResponse> {
  return useQuery({
    queryKey: ANALYTICS_KEY,
    queryFn: () =>
      fetcher<DownloadAnalyticsResponse>("api/download-analytics"),
    staleTime: 60_000,
  });
}

// ---- Categories -----------------------------------------------------------

export interface DownloadCategoriesResponse {
  categories?: Record<string, unknown>;
  [key: string]: unknown;
}

export function useDownloadCategories(): UseQueryResult<DownloadCategoriesResponse> {
  return useQuery({
    queryKey: CATEGORIES_KEY,
    queryFn: () =>
      fetcher<DownloadCategoriesResponse>("api/download-categories"),
    staleTime: 5 * 60_000,
  });
}

export const downloadQueryKeys = {
  active: DOWNLOADS_KEY,
  history: HISTORY_KEY,
  analytics: ANALYTICS_KEY,
  categories: CATEGORIES_KEY,
} as const;

interface FlatActiveItem {
  client: "qbittorrent" | "sabnzbd";
  item: DownloadItem;
}

/** Flatten qbit + sab items into a single ordered list. */
export function flattenActive(
  data: DownloadsResponse | undefined,
): FlatActiveItem[] {
  const out: FlatActiveItem[] = [];
  if (!data) return out;
  if (data.qbittorrent?.items) {
    for (const item of data.qbittorrent.items) {
      out.push({ client: "qbittorrent", item });
    }
  }
  if (data.sabnzbd?.items) {
    for (const item of data.sabnzbd.items) {
      out.push({ client: "sabnzbd", item });
    }
  }
  return out;
}

interface FlatHistoryEntry {
  service: string;
  entry: HistoryEntry;
}

/** Flatten the history map into per-service rows. */
export function flattenHistory(
  data: DownloadHistoryResponse | undefined,
): FlatHistoryEntry[] {
  if (!data?.history) return [];
  const out: FlatHistoryEntry[] = [];
  for (const [service, entries] of Object.entries(data.history)) {
    if (!Array.isArray(entries)) continue;
    for (const entry of entries) {
      if (entry && typeof entry === "object") out.push({ service, entry });
    }
  }
  return out;
}
