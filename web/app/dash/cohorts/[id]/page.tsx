import Link from "next/link";
import { notFound } from "next/navigation";

import { verifySession } from "@/lib/dal";
import { progressSentence, summarise } from "@/lib/roster";

import { getCohort, listParticipants } from "./queries";
import { RosterTable } from "./roster-table";

/**
 * /dash/cohorts/[id] — the roster (plan.md §10, §13, M10).
 *
 * Answers the three questions a counsellor actually opens this screen with:
 * who has not started, who am I waiting on, and whose report can I hand over.
 *
 * What it deliberately does not answer is "why does this student need a
 * follow-up". A counsellor has no read policy on `reviews` at all, because RLS
 * is row-scoped and any policy admitting them would expose `interview_notes` —
 * which §16 treats like health data. "Follow-up needed" is the whole message;
 * the rest is a conversation between two people.
 */
export default async function CohortPage({ params }: PageProps<"/dash/cohorts/[id]">) {
  const { id } = await params;
  await verifySession();

  const cohort = await getCohort(id);
  // Null means RLS did not return it: either no such cohort, or it belongs to
  // another school. Both are a 404 — distinguishing them would confirm that
  // someone else's cohort exists.
  if (!cohort) {
    notFound();
  }

  const participants = await listParticipants(id);
  // Computed once on the server and passed down, so the table's waiting times
  // do not shift between server render and hydration.
  const now = new Date();
  const progress = summarise(participants, now);

  return (
    <main className="mx-auto flex w-full max-w-4xl flex-col gap-8 px-6 py-16">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{cohort.name}</h1>
          <p className="text-sm text-black/60 dark:text-white/60">
            {[cohort.education_level, cohort.intake_year].filter(Boolean).join(" · ")}
          </p>
        </div>

        <div className="flex items-center gap-4 text-sm">
          <Link href={`/dash/cohorts/${id}/upload`} className="underline">
            Upload roster
          </Link>
          <Link href="/dash" className="underline">
            All cohorts
          </Link>
        </div>
      </header>

      {participants.length === 0 ? (
        <p className="text-sm text-black/60 dark:text-white/60">
          No students yet.{" "}
          <Link href={`/dash/cohorts/${id}/upload`} className="underline">
            Upload a roster
          </Link>{" "}
          to add them.
        </p>
      ) : (
        <>
          <section className="flex flex-wrap items-baseline gap-x-6 gap-y-2 text-sm">
            <span className="font-medium">{progressSentence(progress)}</span>
            {progress.notStarted > 0 && (
              <span className="text-black/60 dark:text-white/60">
                {progress.notStarted} not started
              </span>
            )}
            {progress.inProgress > 0 && (
              <span className="text-black/60 dark:text-white/60">
                {progress.inProgress} in progress
              </span>
            )}
            {progress.awaitingReview > 0 && (
              <span className="text-black/60 dark:text-white/60">
                {progress.awaitingReview} awaiting review
              </span>
            )}
            {progress.needsAttention > 0 && (
              <span className="text-red-700 dark:text-red-400">
                {progress.needsAttention} needs attention
              </span>
            )}
          </section>

          {/* §9.4: a stalled queue is "an operational problem to fix". The
              student is never told their report is late; the person who can
              chase a reviewer is. */}
          {progress.overdue > 0 && (
            <p className="rounded border border-amber-500/40 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
              {progress.overdue === 1
                ? "1 student has been waiting more than five days for a review."
                : `${progress.overdue} students have been waiting more than five days for a review.`}{" "}
              Nothing has been said to them about the delay.
            </p>
          )}

          <RosterTable participants={participants} now={now.toISOString()} />
        </>
      )}
    </main>
  );
}
