import "server-only";

import { verifySession } from "@/lib/dal";
import { createClient } from "@/lib/supabase/server";

/**
 * Reads for the dashboard.
 *
 * Kept out of actions.ts on purpose: every export of a `"use server"` file
 * becomes a POST endpoint the browser can call directly, so a read placed there
 * would widen the app's public surface for no reason.
 *
 * These go through the RLS-bound server client rather than the admin client, so
 * the dashboard exercises the same `cohorts_read` and `organisations_read`
 * policies that engine/tests/db/test_rls_cross_tenant.py asserts. A policy
 * regression then shows up as an empty dashboard — visible — instead of a
 * silent cross-tenant leak.
 */

export type CohortRow = {
  id: string;
  name: string;
  intake_year: number | null;
  education_level: string | null;
  created_at: string;
};

export async function listCohorts(): Promise<CohortRow[]> {
  await verifySession();
  const supabase = await createClient();

  // No .eq("organisation_id", …) here — that is `cohorts_read`'s job. If this
  // ever returns another school's cohort, the policy is broken and we want to
  // find that out, not paper over it with a redundant filter.
  const { data } = await supabase
    .from("cohorts")
    .select("id, name, intake_year, education_level, created_at")
    .order("created_at", { ascending: false });

  return data ?? [];
}

export type OrganisationRow = {
  id: string;
  name: string;
  slug: string;
  brand_hex: string | null;
  logo_path: string | null;
};

export async function getOrganisation(): Promise<OrganisationRow | null> {
  await verifySession();
  const supabase = await createClient();

  const { data } = await supabase
    .from("organisations")
    .select("id, name, slug, brand_hex, logo_path")
    .single();

  return data;
}
