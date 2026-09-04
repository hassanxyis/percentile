"use client";

import { useActionState } from "react";

import { createCohort } from "./actions";
import type { ActionState } from "./actions";

/**
 * Minimal cohort creation. M10 designs the real dashboard; this exists so M4's
 * "org/cohort CRUD" is reachable and testable rather than API-only.
 */
export function CreateCohortForm() {
  const [state, formAction, pending] = useActionState<ActionState, FormData>(
    createCohort,
    undefined,
  );

  return (
    <form action={formAction} className="flex flex-col gap-3 border-t border-black/10 pt-6 dark:border-white/10">
      <h2 className="text-sm font-medium">New cohort</h2>

      <label className="flex flex-col gap-1 text-sm">
        <span>Name</span>
        <input
          name="name"
          required
          placeholder="Class of 2027 — Pre-Medical A"
          className="rounded border border-black/20 px-3 py-2 dark:border-white/20"
        />
      </label>

      <div className="flex gap-3">
        <label className="flex flex-1 flex-col gap-1 text-sm">
          <span>Education level</span>
          <select
            name="education_level"
            defaultValue="intermediate"
            className="rounded border border-black/20 px-3 py-2 dark:border-white/20"
          >
            <option value="matric">Matric</option>
            <option value="intermediate">Intermediate</option>
            <option value="undergraduate">Undergraduate</option>
          </select>
        </label>

        <label className="flex w-32 flex-col gap-1 text-sm">
          <span>Intake year</span>
          <input
            name="intake_year"
            inputMode="numeric"
            placeholder="2027"
            className="rounded border border-black/20 px-3 py-2 dark:border-white/20"
          />
        </label>
      </div>

      {state?.error && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {state.error}
        </p>
      )}
      {state?.ok && <p className="text-sm text-green-700 dark:text-green-400">{state.ok}</p>}

      <button
        type="submit"
        disabled={pending}
        className="self-start rounded bg-black px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-black"
      >
        {pending ? "Creating…" : "Create cohort"}
      </button>
    </form>
  );
}
