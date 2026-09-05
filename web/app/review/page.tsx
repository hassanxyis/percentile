import Link from "next/link";

import { requireReviewer } from "@/lib/dal";
import { describeIntendedField } from "@/lib/intended-fields";
import { isOverdue, REVIEW_STATUS_LABEL, waitingFor } from "@/lib/review";

import { listReviewQueue } from "./queries";

/**
 * /review — the psychologist's queue (plan.md §9.1, M8).
 *
 * Oldest first, organisation-scoped, and open only to the roles that
 * `reviews_psychologist_read` admits. §9.3 sizes a reviewer at 15–25 students
 * per pilot week, so this is a working list rather than a dashboard: who is
 * waiting, how long they have waited, and a way in.
 *
 * No scores here. A queue that showed a Holland code beside each name would
 * invite skimming and deciding before opening the session, which is the exact
 * habit the human checkpoint exists to prevent (R9, §9).
 */
export default async function ReviewQueuePage() {
  const session = await requireReviewer();
  const queue = await listReviewQueue();

  // One clock for the whole render. Calling `new Date()` per row would let a
  // slow render put two students submitted a second apart into different day
  // buckets.
  const now = new Date();
  const overdue = queue.filter((row) => isOverdue(row.submitted_at, now)).length;

  return (
    <main className="mx-auto flex w-full max-w-4xl flex-col gap-8 px-6 py-16">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Review queue</h1>
          <p className="text-sm text-black/60 dark:text-white/60">
            {session.fullName} · {session.role.replace("_", " ")}
          </p>
        </div>

        <div className="flex items-center gap-4 text-sm">
          <Link href="/dash" className="underline">
            Cohorts
          </Link>
          <form action="/logout" method="post">
            <button type="submit" className="underline">
              Sign out
            </button>
          </form>
        </div>
      </header>

      {/* §9.4: a backlog is an operational problem at this end, never something
          the student is told about. Stated plainly to whoever can act on it. */}
      {overdue > 0 && (
        <p
          role="alert"
          className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm"
        >
          {overdue === 1
            ? "1 student has been waiting more than five days."
            : `${overdue} students have been waiting more than five days.`}
        </p>
      )}

      {queue.length === 0 ? (
        <p className="text-sm text-black/60 dark:text-white/60">
          Nothing is waiting for review.
        </p>
      ) : (
        <table className="text-sm">
          <thead>
            <tr className="text-left text-xs uppercase text-black/50 dark:text-white/50">
              <th className="py-2 pr-4 font-medium">Student</th>
              <th className="py-2 pr-4 font-medium">Cohort</th>
              <th className="py-2 pr-4 font-medium">Intended field</th>
              <th className="py-2 pr-4 font-medium">Waiting</th>
              <th className="py-2 font-medium">State</th>
            </tr>
          </thead>
          <tbody>
            {queue.map((row) => {
              const late = isOverdue(row.submitted_at, now);
              return (
                <tr
                  key={row.session_id}
                  className="border-t border-black/10 dark:border-white/10"
                >
                  <td className="py-2 pr-4">
                    <Link
                      href={`/review/${row.session_id}`}
                      className="font-medium underline"
                    >
                      {row.full_name}
                    </Link>
                  </td>
                  <td className="py-2 pr-4 text-black/60 dark:text-white/60">
                    {row.cohort_name}
                  </td>
                  <td className="py-2 pr-4 text-black/60 dark:text-white/60">
                    {describeIntendedField(row.intended_field)}
                  </td>
                  <td
                    className={`py-2 pr-4 tabular-nums ${
                      late ? "font-medium text-amber-700 dark:text-amber-400" : ""
                    }`}
                  >
                    {waitingFor(row.submitted_at, now)}
                  </td>
                  <td className="py-2 text-black/60 dark:text-white/60">
                    {row.review_status
                      ? (REVIEW_STATUS_LABEL[row.review_status] ?? row.review_status)
                      : "Not started"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </main>
  );
}
