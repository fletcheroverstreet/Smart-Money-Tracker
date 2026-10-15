"use client";

/**
 * The sign-in panel on /private.
 *
 * - GET  /api/private/session  -> { active, configured }
 * - POST /api/private/login    body { passphrase }  -> 200, 401 (wrong), 503 (not set up)
 * - POST /api/private/logout
 *
 * The passphrase is read from the form only at the moment of sending and the
 * field is cleared straight after. It is never put in React state,
 * localStorage or the URL. The server's answer is an HttpOnly cookie that
 * this code can't see; it only learns "signed in or not" from /session.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId } from "react";

import { ApiError, apiGet, apiUrl } from "@/lib/api/client";
import type { SessionOut } from "@/lib/api/types";

/** Shared with the ticker chart (components/tickers/EventTimeline.tsx), so signing in updates it too. */
const SESSION_KEY = ["private-session"];

/** POST to the private-mode API. Same-origin, so the browser sends and stores the cookie itself. */
async function post(path: string, body?: unknown): Promise<SessionOut> {
  const response = await fetch(apiUrl(path), {
    method: "POST",
    credentials: "same-origin",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new ApiError(response.status, response.statusText);
  return (await response.json()) as SessionOut;
}

/** Plain words for a failed sign-in. */
function loginErrorText(error: unknown): string {
  if (error instanceof ApiError && error.status === 401) return "That passphrase didn't match. Nothing was saved.";
  if (error instanceof ApiError && error.status === 503) return "Private mode isn't set up on this server.";
  if (error instanceof ApiError && error.status === 422) return "Enter the passphrase first.";
  return "The server didn't answer. Try again in a minute.";
}

const panel = "rounded-[var(--radius-panel)] border border-hairline bg-console px-4 py-4";
const buttonClass =
  "inline-flex h-11 items-center rounded-[var(--radius-control)] border border-rule-strong px-4 hover:bg-raised disabled:text-dim md:h-9";

export function PrivateMode() {
  const queryClient = useQueryClient();
  const fieldId = useId();
  const errorId = useId();

  const session = useQuery({
    queryKey: SESSION_KEY,
    queryFn: () => apiGet<SessionOut>("/api/private/session", undefined, { cache: "no-store" }),
    retry: false,
  });

  // After signing in or out: store the new state, and drop any cached prices so
  // nothing licensed stays on screen after signing out.
  const settle = (next: SessionOut) => {
    queryClient.setQueryData(SESSION_KEY, next);
    queryClient.removeQueries({ queryKey: ["private-prices"] });
  };

  const login = useMutation({
    mutationFn: (passphrase: string) => post("/api/private/login", { passphrase }),
    onSuccess: settle,
  });
  const logout = useMutation({
    mutationFn: () => post("/api/private/logout"),
    onSuccess: settle,
  });

  const onSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const value = new FormData(form).get("passphrase");
    // Clear the field at once: the passphrase lives only in this one request.
    form.reset();
    if (typeof value === "string" && value !== "") login.mutate(value);
  };

  if (session.isPending) {
    return (
      <section aria-busy="true" className={panel}>
        <span className="sr-only">Checking whether you&apos;re signed in…</span>
        <div className="skeleton h-6 w-40" />
        <div className="skeleton mt-3 h-11 w-full max-w-sm md:h-9" />
      </section>
    );
  }

  if (session.isError) {
    return (
      <section role="alert" className={panel}>
        <p className="flex items-center gap-2 font-medium">
          <span aria-hidden className="text-beacon">!</span>
          Couldn&apos;t check the sign-in
        </p>
        <p className="mt-1 text-dim">The data service didn&apos;t answer. Private mode needs it to sign you in.</p>
        <button type="button" onClick={() => void session.refetch()} className={`${buttonClass} mt-3`}>
          Try again
        </button>
      </section>
    );
  }

  const state = session.data;

  if (!state.configured) {
    return (
      <section aria-labelledby="signin-title" className={panel}>
        <h2 id="signin-title" className="font-display text-[22px] font-medium leading-7">
          Private mode isn&apos;t set up on this server
        </h2>
        {/* How the owner turns it on is in the README, not on a public page. */}
        <p className="mt-2 text-dim">There&apos;s nothing to sign in to here. Everything public works as usual.</p>
      </section>
    );
  }

  if (state.active) {
    return (
      <section aria-labelledby="signin-title" className={panel}>
        <h2 id="signin-title" className="font-display text-[22px] font-medium leading-7">
          <span aria-hidden>✓ </span>You&apos;re in private mode
        </h2>
        <p className="mt-2 text-dim">
          Price lines now show on ticker charts in this browser. Signing out removes the cookie and hides them again.
        </p>
        <button
          type="button"
          onClick={() => logout.mutate()}
          disabled={logout.isPending}
          className={`${buttonClass} mt-3`}
        >
          {logout.isPending ? "Signing out…" : "Sign out"}
        </button>
        <p role="status" className="mt-2 text-[13px] text-beacon">
          {logout.isError ? (
            <>
              <span aria-hidden>! </span>Signing out didn&apos;t go through. Try again.
            </>
          ) : null}
        </p>
      </section>
    );
  }

  return (
    <section aria-labelledby="signin-title" className={panel}>
      <h2 id="signin-title" className="font-display text-[22px] font-medium leading-7">
        Sign in
      </h2>
      <p className="mt-1 text-dim">For the site&apos;s owner only. There are no other accounts.</p>
      <form onSubmit={onSubmit} className="mt-3 flex flex-col gap-2">
        <label htmlFor={fieldId} className="text-[13px] font-medium">
          Passphrase
        </label>
        <div className="flex flex-wrap gap-2">
          <input
            id={fieldId}
            name="passphrase"
            type="password"
            required
            autoComplete="current-password"
            aria-invalid={login.isError || undefined}
            aria-describedby={login.isError ? errorId : undefined}
            className="h-11 min-w-0 flex-1 basis-56 rounded-[var(--radius-control)] border border-rule-strong bg-night px-3 md:h-9"
          />
          <button type="submit" disabled={login.isPending} className={buttonClass}>
            {login.isPending ? "Signing in…" : "Sign in"}
          </button>
        </div>
        <p id={errorId} role="status" className="min-h-5 text-[13px] text-beacon">
          {login.isError ? (
            <>
              <span aria-hidden>! </span>
              {loginErrorText(login.error)}
            </>
          ) : null}
        </p>
      </form>
    </section>
  );
}
