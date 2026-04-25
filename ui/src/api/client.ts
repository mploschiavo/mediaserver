// Typed fetch wrapper for the Media Stack Controller API.
//
// Goals:
// - Single `fetcher<T>(path, init)` that throws ApiError on non-2xx and
//   returns parsed JSON on 2xx.
// - Threads the session cookie (`credentials: "same-origin"`).
// - Auto-generates `Idempotency-Key` for mutations when omitted.
// - Surfaces 401 via an event bus so the layout shell decides what to
//   do (redirect, modal, toast). The client never navigates.

import { newIdempotencyKey } from "@/lib/idempotency";

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;
  readonly idempotencyKey?: string;

  constructor(
    message: string,
    status: number,
    body: unknown,
    idempotencyKey?: string,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
    if (idempotencyKey !== undefined) {
      this.idempotencyKey = idempotencyKey;
    }
  }
}

export interface FetcherInit extends Omit<RequestInit, "body"> {
  body?: BodyInit | null;
  // When set, sent verbatim. When omitted on mutating methods, a fresh
  // UUID is generated. Pass an empty string to opt out entirely.
  idempotencyKey?: string;
}

export type AuthEvent = "unauthenticated";
type AuthListener = (event: AuthEvent) => void;

const authListeners = new Set<AuthListener>();

export function onAuthEvent(listener: AuthListener): () => void {
  authListeners.add(listener);
  return () => authListeners.delete(listener);
}

function emitAuth(event: AuthEvent): void {
  for (const listener of authListeners) {
    try {
      listener(event);
    } catch {
      // Listeners must not break the request flow.
    }
  }
}

let baseUrl = "";

export function setBaseUrl(url: string): void {
  baseUrl = url.replace(/\/+$/, "");
}

export function getBaseUrl(): string {
  return baseUrl;
}

const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

// Double-submit CSRF protection. The controller issues a cookie
// `media_stack_csrf=<token>` on every authenticated GET (see
// `core/auth/csrf.py::CsrfProtector`); mutating requests must echo
// the same token in the `X-CSRF-Token` header. The cookie is HttpOnly
// = false specifically so the SPA can read it back. Without this
// header the controller returns 403 "CSRF token missing or invalid"
// — which is the bug that surfaced on Snapshots → "Take snapshot now"
// in v1.3.2 (see auth-redirect.test.ts for the related pattern).
const _CSRF_COOKIE_NAME = "media_stack_csrf";
const _CSRF_HEADER_NAME = "X-CSRF-Token";

function readCsrfTokenFromCookie(): string | undefined {
  if (typeof document === "undefined") return undefined;
  const raw = document.cookie || "";
  // Naive parse — cookie values shouldn't contain `;` or `=`. Use
  // String.prototype.split rather than a regex to keep this cheap
  // (called on every mutating request).
  for (const piece of raw.split(";")) {
    const eq = piece.indexOf("=");
    if (eq < 0) continue;
    const name = piece.slice(0, eq).trim();
    if (name === _CSRF_COOKIE_NAME) {
      return decodeURIComponent(piece.slice(eq + 1).trim());
    }
  }
  return undefined;
}

function buildUrl(path: string): string {
  if (!baseUrl) return path;
  return `${baseUrl}/${path.replace(/^\/+/, "")}`;
}

function isJsonResponse(res: Response): boolean {
  const ct = res.headers.get("content-type") ?? "";
  return ct.toLowerCase().includes("application/json");
}

async function readBody(res: Response): Promise<unknown> {
  if (res.status === 204) return undefined;
  if (!isJsonResponse(res)) {
    return await res.text().catch(() => "");
  }
  const raw = await res.text();
  if (!raw) return undefined;
  try {
    return JSON.parse(raw) as unknown;
  } catch (err) {
    throw new ApiError(
      `Failed to parse JSON response: ${(err as Error).message}`,
      res.status,
      raw,
    );
  }
}

export async function fetcher<T>(
  path: string,
  init: FetcherInit = {},
): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (!headers.has("Accept")) headers.set("Accept", "application/json");
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let idempotencyKey: string | undefined;
  if (MUTATING_METHODS.has(method)) {
    if (init.idempotencyKey === undefined) {
      idempotencyKey = newIdempotencyKey();
      headers.set("Idempotency-Key", idempotencyKey);
    } else if (init.idempotencyKey !== "") {
      idempotencyKey = init.idempotencyKey;
      headers.set("Idempotency-Key", idempotencyKey);
    }
    // Double-submit CSRF: copy the cookie's token into the header
    // unless the caller already set one. The controller's CSRF
    // protector compares the two and 403s on mismatch.
    if (!headers.has(_CSRF_HEADER_NAME)) {
      const token = readCsrfTokenFromCookie();
      if (token) headers.set(_CSRF_HEADER_NAME, token);
    }
  }

  const { idempotencyKey: _drop, ...rest } = init;
  void _drop;

  const res = await fetch(buildUrl(path), {
    ...rest,
    method,
    headers,
    credentials: rest.credentials ?? "same-origin",
  });

  if (res.status === 401) emitAuth("unauthenticated");

  const body = await readBody(res);
  if (!res.ok) {
    const message =
      (typeof body === "object" && body !== null && "error" in body
        ? String((body as { error: unknown }).error)
        : res.statusText) || `HTTP ${res.status}`;
    throw new ApiError(message, res.status, body, idempotencyKey);
  }
  return body as T;
}
