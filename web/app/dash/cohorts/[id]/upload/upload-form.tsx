"use client";

import Link from "next/link";
import { useActionState } from "react";

import { ROSTER_TEMPLATE } from "@/lib/roster-csv";

import { importRoster, type ImportedInvite, type ImportState } from "./actions";

/**
 * Roster upload (plan.md §13, M5).
 *
 * Three states worth designing for, not two: a file-level failure, a list of
 * per-row problems, and success with links that exist only in this response.
 */

function download(filename: string, contents: string) {
  // Built in the browser from data already in memory. A server round trip would
  // put invite tokens in a URL or a temporary file; these are credentials for
  // minors' records (§16), so they stay in the page.
  const url = URL.createObjectURL(new Blob([contents], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function invitesCsv(invites: ImportedInvite[]): string {
  const escape = (value: string) => `"${value.replace(/"/g, '""')}"`;
  return [
    "full_name,email,invite_url",
    ...invites.map((i) => [i.full_name, i.email, i.invite_url].map(escape).join(",")),
  ].join("\n");
}

export function UploadForm({ cohortId, cohortName }: { cohortId: string; cohortName: string }) {
  const [state, formAction, pending] = useActionState<ImportState, FormData>(
    importRoster,
    { status: "idle" },
  );

  if (state.status === "done") {
    return (
      <div className="flex flex-col gap-4">
        <p className="text-sm">
          Imported <strong>{state.count}</strong> students into {cohortName}.
        </p>

        <div className="flex flex-col gap-3 rounded border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm">
          <p className="font-medium">Download the invite links now.</p>
          <p>
            Only a fingerprint of each link is stored, so this file cannot be
            produced again. If you lose it, the students need fresh invitations.
          </p>
          <p className="text-black/70 dark:text-white/70">
            Automatic invitation emails are not switched on yet. Until they are,
            send these links yourself. Once the emails do go out they will carry
            new links, and the ones in this file will stop working.
          </p>
          <button
            type="button"
            onClick={() =>
              download(`invite-links-${cohortId}.csv`, invitesCsv(state.invites))
            }
            className="self-start rounded bg-black px-4 py-2 text-sm font-medium text-white dark:bg-white dark:text-black"
          >
            Download invite links
          </button>
        </div>

        <Link href={`/dash/cohorts/${cohortId}`} className="text-sm underline">
          Back to the roster
        </Link>
      </div>
    );
  }

  return (
    <form action={formAction} className="flex flex-col gap-4">
      <input type="hidden" name="cohort_id" value={cohortId} />

      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium">Roster CSV</span>
        <input
          type="file"
          name="file"
          accept=".csv,text/csv"
          required
          className="rounded border border-black/20 px-3 py-2 dark:border-white/20"
        />
      </label>

      <details className="text-sm">
        <summary className="cursor-pointer">What should the file look like?</summary>
        <div className="mt-2 flex flex-col gap-2">
          <p className="text-black/70 dark:text-white/70">
            A header row, then one student per row. <code>full_name</code> and{" "}
            <code>email</code> are required; <code>external_ref</code> (roll
            number), <code>intended_field</code> and <code>education_level</code>{" "}
            are optional. Any other columns are ignored.
          </p>
          <pre className="overflow-x-auto rounded bg-black/5 p-3 text-xs dark:bg-white/10">
            {ROSTER_TEMPLATE}
          </pre>
          <button
            type="button"
            onClick={() => download("roster-template.csv", ROSTER_TEMPLATE)}
            className="self-start underline"
          >
            Download a template
          </button>
        </div>
      </details>

      {state.status === "error" && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {state.message}
        </p>
      )}

      {state.status === "invalid" && (
        <div role="alert" className="flex flex-col gap-2">
          <p className="text-sm text-red-600 dark:text-red-400">
            Nothing was imported. {state.errors.length}{" "}
            {state.errors.length === 1 ? "problem" : "problems"} to fix:
          </p>
          {/* Every problem at once. Row numbers are the ones the counsellor
              sees in Excel, so the list can be worked straight down. */}
          <table className="text-sm">
            <thead>
              <tr className="text-left text-xs uppercase text-black/50 dark:text-white/50">
                <th className="py-1 pr-4 font-medium">Row</th>
                <th className="py-1 pr-4 font-medium">Column</th>
                <th className="py-1 font-medium">Problem</th>
              </tr>
            </thead>
            <tbody>
              {state.errors.map((error, index) => (
                <tr key={index} className="border-t border-black/10 dark:border-white/10">
                  <td className="py-1 pr-4 tabular-nums">{error.row}</td>
                  <td className="py-1 pr-4 font-mono text-xs">{error.column}</td>
                  <td className="py-1">{error.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <button
        type="submit"
        disabled={pending}
        className="self-start rounded bg-black px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-black"
      >
        {pending ? "Importing…" : "Import roster"}
      </button>
    </form>
  );
}
