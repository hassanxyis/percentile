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

export async function listParticipants(cohortId: string): Promise<ParticipantRow[]> {
  await verifySession();
  const supabase = await createClient();

  const { data } = await supabase
    .from("participants")
    .select("id, full_name, email, external_ref, intended_field, status")
    .eq("cohort_id", cohortId)
    .order("full_name");

  return data ?? [];
}
