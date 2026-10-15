/**
 * API calls made while the Next.js server renders a page. Server Components only.
 *
 * The API limits how fast each visitor may call it (backend/src/smt/api/middleware.py).
 * A page render reaches the API from the web server's own private address, and
 * the API treats a private caller that names no visitor as internal and never
 * limits it. So these helpers name the visitor: they pass the visitor's IP
 * address along as X-Forwarded-For. Someone loading pages very fast is then
 * slowed down exactly like someone calling the API directly.
 *
 * This file reads the incoming page request (next/headers), which only works
 * on the server; browser code keeps using ./client.
 */
import { headers } from "next/headers";

import { apiGet as clientGet, type Params } from "./client";

export { ApiError } from "./client";

/** Digits, hex letters, dots and colons only: an IPv4 or IPv6 address and nothing else. */
const IP_ADDRESS = /^[0-9A-Fa-f:.]{2,45}$/;

/**
 * Sent in place of an address when the page request names its visitor in a way
 * that can't be read. The API can't read it either, so it limits these renders
 * together in one bucket instead of treating them as internal calls.
 */
const UNREADABLE = "unknown";

/**
 * Who to name as the visitor, read from the page request.
 *
 * Caddy writes the visitor's address into X-Forwarded-For (it ignores any value
 * the visitor sent), and Next.js adds it itself when there is no proxy, as in
 * local development. The first entry is the visitor; any later ones are
 * proxies. X-Real-IP is the fallback some proxies use instead.
 *
 * Returns the address; UNREADABLE when a header is there but neither holds an
 * address (still rate-limited, see above); null only when the request names no
 * visitor at all, which the API treats as an internal call. Both Caddy and
 * Next.js always set X-Forwarded-For, so null doesn't happen for a page view.
 *
 * Only Caddy makes this trustworthy. Next.js adds the header only when it is
 * missing, so someone reaching the Next.js server directly (local development,
 * no Caddy) could name any address they like. In production only Caddy's
 * ports are open (infra/docker-compose.yml).
 */
async function visitorIp(): Promise<string | null> {
  const incoming = await headers();
  const first = incoming.get("x-forwarded-for")?.split(",")[0]?.trim() ?? "";
  const realIp = incoming.get("x-real-ip")?.trim() ?? "";
  for (const candidate of [first, realIp]) {
    if (IP_ADDRESS.test(candidate)) return candidate;
  }
  return incoming.has("x-forwarded-for") || incoming.has("x-real-ip") ? UNREADABLE : null;
}

/** `init` plus an X-Forwarded-For header naming the visitor (when the request names one). */
async function withVisitor(init?: RequestInit): Promise<RequestInit> {
  const ip = await visitorIp();
  const merged = new Headers(init?.headers);
  if (ip) merged.set("X-Forwarded-For", ip);
  return { ...init, headers: merged };
}

/** GET JSON while rendering a page. Throws ApiError with the API's own message on failure. */
export async function apiGet<T>(path: string, params?: Params, init?: RequestInit): Promise<T> {
  return clientGet<T>(path, params, await withVisitor(init));
}

/** GET that never throws: returns null when the API is down, so pages can render an honest state. */
export async function apiGetOrNull<T>(path: string, params?: Params): Promise<T | null> {
  // Read the page request before the try block: when Next.js builds the site it
  // signals "this page depends on the request" by throwing from headers(), and
  // that signal must not be swallowed by the catch below.
  const init = await withVisitor({ cache: "no-store" });
  try {
    return await clientGet<T>(path, params, init);
  } catch {
    return null;
  }
}
