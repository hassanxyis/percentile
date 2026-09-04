import Link from "next/link";
import { notFound } from "next/navigation";

import { verifySession } from "@/lib/dal";
import { describeIntendedField } from "@/lib/intended-fields";

import { getCohort, listParticipants } from "./queries";

/**
 * /dash/cohorts/[id] — the roster (plan.md §13).
 *
 * Deliberately plain. M10 owns the counsellor dashboard: progress vocabulary,
 * resend, report downloads, branding. M5 needs somewhere an import visibly
 * lands, so this lists who is on the roster and what state each student is in.
 */

/** Plain-language status. The vocabulary in §5 is for the system, not a reader. */
const STATUS_LABEL: Record<string, string> = {
  invited: "Invited",
  started: "In progress",
  submitted: "Submitted",
  scored: "Awaiting review",
  pending_review: "Awaiting review",
  reviewed: "Being reviewed",
  needs_more_info: "Follow-up needed",
  confirmed: "Complete",
  failed: "Problem",
};

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

  return (
    <main className="mx-auto flex w-full max-w-3xl flex-col gap-8 px-6 py-16">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{cohort.name}</h1>
          <p className="text-sm text-black/60 dark:text-white/60">
            {[cohort.education_level, cohort.intake_year].filter(Boolean).join(" · ")}
            {participants.length > 0 &&
              ` · ${participants.length} ${participants.length === 1 ? "student" : "students"}`}
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
        <table className="text-sm">
          <thead>
            <tr className="text-left text-xs uppercase text-black/50 dark:text-white/50">
              <th className="py-2 pr-4 font-medium">Name</th>
              <th className="py-2 pr-4 font-medium">Roll no.</th>
              <th className="py-2 pr-4 font-medium">Intended field</th>
              <th className="py-2 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {participants.map((participant) => (
              <tr
                key={participant.id}
                className="border-t border-black/10 dark:border-white/10"
              >
                <td className="py-2 pr-4">{participant.full_name}</td>
                <td className="py-2 pr-4 text-black/60 dark:text-white/60">
                  {participant.external_ref ?? "—"}
                </td>
                <td className="py-2 pr-4 text-black/60 dark:text-white/60">
                  {describeIntendedField(participant.intended_field)}
                </td>
                <td className="py-2">
                  {STATUS_LABEL[participant.status] ?? participant.status}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
