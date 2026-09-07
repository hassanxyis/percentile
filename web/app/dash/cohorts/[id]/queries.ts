import "server-only";

import { verifySession } from "@/lib/dal";
import { createClient } from "@/lib/supabase/server";

/**
 * Reads for one cohort.
 *
 * Separate from `actions.ts` for the reason given in `app/dash/queries.ts`:
 * every export of a `"use server"` file is a POST endpoint the browser can
 * call directly.
 *
 * Both go through the RLS-bound client. `cohorts_read` and `participants_read`
 * in 0002_rls.sql already scope these to the caller's organisation, and
 * `engine/tests/db/test_rls_cross_tenant.py` asserts exactly that — so a policy
 * regression surfaces here as a missing cohort rather than as another school's
 * roster.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * THIS FILE MUST NEVER SELECT FROM `reviews`.
 *
 * A counsellor has no read policy on that table at all (0003_reviews.sql) —
 * deliberately, because RLS is row-scoped rather than column-scoped, so any
 * SELECT policy admitting a counsellor would hand them `interview_notes` too.
 * §16 treats those notes like health data.
 *
 * The supported path is `review_progress_for_session()`, a SECURITY DEFINER
 * function returning status and confirmed_at and nothing else. A `.select()`
 * with a `reviews(...)` embed here would return null for a counsellor and the
 * real row for an org_admin — passing every test run as an admin, and silently
 * blanking the column for the role that actually uses this screen.
 * ─────────────────────────────────────────────────────────────────────────────
 */

export type CohortDetail = {
  id: string;
  name: string;
  intake_year: number | null;
  education_level: string | null;
};

export type ParticipantRow = {
  id: string;
  full_name: string;
  email: string | null;
  external_ref: string | null;
  intended_field: string | null;
  status: string;
  /** When they finished. Drives the waiting time and the §9.4 backlog flag. */
  submitted_at: string | null;
  /** The student's report, once one exists. Null until M9 renders it. */
  report_id: string | null;
};

/** The cohort, or null when it is not the caller's to see. */
export async function getCohort(id: string): Promise<CohortDetail | null> {
  await verifySession();
  const supabase = await createClient();

  // No organisation filter — that is `cohorts_read`'s job. A cohort belonging
  // to another school returns no row, which the page turns into a 404.
  const { data } = await supabase
    .from("cohorts")
    .select("id, name, intake_year, education_level")
    .eq("id", id)
    .maybeSingle();

  return data;
}

/**
 * The roster, with each student's progress and report availability.
 *
 * Three queries rather than one embedded select, because the three tables are
 * joined through `sessions` in a shape PostgREST's embedding cannot express in
 * one hop — and because a failed embed returns null rather than erroring, which
 * would render an empty column and look like "no reports yet".
 */
export async function listParticipants(cohortId: string): Promise<ParticipantRow[]> {
  await verifySession();
  const supabase = await createClient();

  const { data: participants } = await supabase
    .from("participants")
    .select("id, full_name, email, external_ref, intended_field, status")
    .eq("cohort_id", cohortId)
    .order("full_name");

  if (!participants?.length) {
    return [];
  }

  const ids = participants.map((participant) => participant.id);

  const { data: sessions } = await supabase
    .from("sessions")
    .select("id, participant_id, submitted_at")
    .in("participant_id", ids);

  const sessionByParticipant = new Map(
    (sessions ?? []).map((session) => [session.participant_id, session]),
  );

  // `reports_read` (0002_rls.sql) admits any authenticated user in the owning
  // organisation, counsellors included — the PDF is what they hand the student,
  // and it carries no flags (R8). Only the id is read: `storage_path` is not
  // rendered anywhere, and the download action re-derives it server-side.
  const sessionIds = (sessions ?? []).map((session) => session.id);
  const { data: reports } = sessionIds.length
    ? await supabase
        .from("reports")
        .select("id, session_id, created_at")
        .eq("kind", "student")
        .in("session_id", sessionIds)
        .order("created_at", { ascending: false })
    : { data: [] };

  // Newest first above, so the first row per session wins — a re-render under a
  // new template version writes a second row (0010_student_reports.sql) and the
  // counsellor should get the current one.
  const reportBySession = new Map<string, string>();
  for (const report of reports ?? []) {
    if (!reportBySession.has(report.session_id)) {
      reportBySession.set(report.session_id, report.id);
    }
  }

  return participants.map((participant) => {
    const session = sessionByParticipant.get(participant.id);
    return {
      ...participant,
      submitted_at: session?.submitted_at ?? null,
      report_id: session ? (reportBySession.get(session.id) ?? null) : null,
    };
  });
}
