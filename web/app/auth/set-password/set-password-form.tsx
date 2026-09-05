"use client";

import { useActionState } from "react";

import { setPassword, type SetPasswordState } from "./actions";

const FIELD = "rounded border border-black/20 px-3 py-2 dark:border-white/20";

/**
 * Password entry for a newly invited member.
 *
 * `autoComplete="new-password"` on both fields so a password manager offers to
 * generate and store one rather than autofilling an existing credential.
 */
export function SetPasswordForm() {
  const [state, formAction, pending] = useActionState<SetPasswordState, FormData>(
    setPassword,
    undefined,
  );

  return (
    <form action={formAction} className="flex flex-col gap-4">
      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium">New password</span>
        <input
          type="password"
          name="password"
          autoComplete="new-password"
          minLength={8}
          required
          className={FIELD}
        />
      </label>

      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium">Confirm password</span>
        <input
          type="password"
          name="confirm_password"
          autoComplete="new-password"
          minLength={8}
          required
          className={FIELD}
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
        className="self-start rounded bg-black px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-black"
      >
        {pending ? "Saving…" : "Save password"}
      </button>
    </form>
  );
}
