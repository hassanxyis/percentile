"use client";

import { useActionState } from "react";

import { login, type LoginState } from "./actions";

/**
 * Sign-in form for counsellors, psychologists and org admins.
 *
 * Students never see this — they are never authenticated at all, and reach the
 * assessment through a one-time token at /a/[token] (plan §5, 0002_rls.sql).
 */
export function LoginForm({ next }: { next: string }) {
  const [state, formAction, pending] = useActionState<LoginState, FormData>(
    login,
    undefined,
  );

  return (
    <form action={formAction} className="flex flex-col gap-4">
      <input type="hidden" name="next" value={next} />

      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium">Email</span>
        <input
          type="email"
          name="email"
          autoComplete="email"
          required
          className="rounded border border-black/20 px-3 py-2 dark:border-white/20"
        />
      </label>

      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium">Password</span>
        <input
          type="password"
          name="password"
          autoComplete="current-password"
          required
          className="rounded border border-black/20 px-3 py-2 dark:border-white/20"
        />
      </label>

      {state?.error && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {state.error}
        </p>
      )}

      <button
        type="submit"
        disabled={pending}
        className="rounded bg-black px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-black"
      >
        {pending ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}
