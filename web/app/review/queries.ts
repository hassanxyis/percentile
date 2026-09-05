import "server-only";

import { requireReviewer } from "@/lib/dal";
import { createClient } from "@/lib/supabase/server";

/**
 * Reads for the psychologist review portal (plan.md §9, M8).
 *
 * Through the RLS-bound client, like every other `queries.ts` here and unlike
 * the taker's. That is deliberate and load-bearing on this route in particular:
 * `reviews_psychologist_read` (0003_reviews.sql) is the policy that keeps
 * `interview_notes` away from a counsellor, and
 * `engine/tests/db/test_rls_reviews.py` asserts exactly that boundary. Reading
 * through the same client means the portal exercises the policy the tests
 * prove, so a regression shows up as an empty screen rather than as a leak.
 *
 * Using the admin client here would bypass all of it — and this is the one
 * screen in the product where the data is notes about a minor that §16 says to
 * treat like health data.
 */

export type QueueRow = {
  session_id: string;
  participant_id: string;
  full_name: string;
  intended_field: string | null;
  cohort_name: string;
  submitted_at: string | null;
  status: string;
  review_status: string | null;
};

type QueueQueryRow = {
  id: string;
  submitted_at: string | null;
  participants: {
    id: string;
    full_name: string;
    intended_field: string | null;
    status: string;
    cohorts: { name: string } | null;
  } | null;
  reviews: { status: string }[] | null;
};

/**
 * Sessions awaiting a decision, oldest first (§9.1).
 *
 * "Awaiting" is three participant statuses, not one:
 *
 *   - `pending_review`  — scored, nobody has opened it
 *   - `reviewed`        — a draft is saved and someone must come back to it
 *   - `needs_more_info` — sent back for a second conversation (§9.1)
 *
 * `confirmed` is deliberately absent: that work is done. So is `submitted`,
 * which has not been scored yet and has nothing to show a reviewer.
 *
 * No organisation filter — that is RLS's job, via the `sessions_read` and
 * `participants_read` policies. If this ever returns another school's student,
 * the policy is broken and we want to see it rather than paper over it with a
 * redundant `.eq()`.
 */
const AWAITING_STATUSES = ["pending_review", "reviewed", "needs_more_info"] as const;

export async function listReviewQueue(): Promise<QueueRow[]> {
  await requireReviewer();
  const supabase = await createClient();

  const { data } = await supabase
    .from("sessions")
    .select(
      "id, submitted_at, " +
        "participants!inner(id, full_name, intended_field, status, cohorts(name)), " +
        "reviews(status)",
    )
    // `!inner` above makes this filter on the joined row rather than returning
    // sessions with a null participant. PostgREST defaults to a left join, and
    // a filter on a left-joined column silently matches nothing.
    .in("participants.status", [...AWAITING_STATUSES])
    // Oldest first (§9.1). Nulls last: a session with no submitted_at cannot be
    // awaiting review, but if one appears it belongs at the bottom rather than
    // at the top of a queue sorted by how long people have waited.
    .order("submitted_at", { ascending: true, nullsFirst: false })
    .returns<QueueQueryRow[]>();

  return (data ?? [])
    .filter((row) => row.participants !== null)
    .map((row) => ({
      session_id: row.id,
      participant_id: row.participants!.id,
      full_name: row.participants!.full_name,
      intended_field: row.participants!.intended_field,
      cohort_name: row.participants!.cohorts?.name ?? "—",
      submitted_at: row.submitted_at,
      status: row.participants!.status,
      // `reviews` is unique on session_id (0003), so this is 0 or 1 rows.
      review_status: row.reviews?.[0]?.status ?? null,
    }));
}
