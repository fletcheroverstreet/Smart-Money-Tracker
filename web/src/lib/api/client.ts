/**
 * How the web app talks to the API.
 *
 * In the browser, requests go to the same origin (/api/...). Caddy (production)
 * or Next's rewrites (local) forward them to FastAPI. On the server (React
 * Server Components), requests go straight to API_INTERNAL_URL.
 *
 * Pages rendered on the server must import from ./server instead: it wraps
 * these calls and passes the visitor's IP address to the API, so the API's
 * per-visitor rate limit also covers page renders.
 */

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export type Params = Record<string, string | number | boolean | null | undefined>;

function baseUrl(): string {
  if (typeof window !== "undefined") return "";
  return process.env.API_INTERNAL_URL ?? "http://127.0.0.1:8000";
}

export function apiUrl(path: string, params?: Params): string {
  const url = new URL(path, "http://placeholder");
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, String(value));
  }
  return `${baseUrl()}${url.pathname}${url.search}`;
}

/** GET JSON. Throws ApiError with the API's own message on failure. Headers in `init` are kept. */
export async function apiGet<T>(path: string, params?: Params, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const response = await fetch(apiUrl(path, params), { ...init, headers });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* not JSON; keep the status text */
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}
