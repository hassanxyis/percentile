"use server";

import { revalidatePath } from "next/cache";

import { requireRole } from "@/lib/dal";
import { createAdminClient } from "@/lib/supabase/admin";

/**
 * Organisation and cohort writes (plan §13, M4).
 *
 * Two rules govern every action in this file.
 *
 * **Writes go through the admin client.** 0002_rls.sql grants authenticated
 * users SELECT and nothing else — "no insert/update/delete policy exists for
 * authenticated users anywhere in this file" — so a write with the RLS-bound
 * client would simply fail. All mutation is server-side by design.
 *
 * **The organisation_id comes from the session, never from the request.** That
 * is what replaces the RLS the admin client bypasses. A server action is
 * reachable by direct POST, not only through our own form
 * (node_modules/next/dist/docs/01-app/01-getting-started/07-mutating-data.md),
 * so an organisation_id field in a FormData would let any authenticated
 * counsellor write into any school's records.
 */

export type ActionState = { error?: string; ok?: string } | undefined;

const EDUCATION_LEVELS = ["matric", "intermediate", "undergraduate"] as const;

export async function createCohort(
  _state: ActionState,
  formData: FormData,
): Promise<ActionState> {
  const session = await requireRole("counsellor", "org_admin", "superadmin");

  const name = String(formData.get("name") ?? "").trim();
  const educationLevel = String(formData.get("education_level") ?? "").trim();
  const intakeYearRaw = String(formData.get("intake_year") ?? "").trim();

  if (!name) {
    return { error: "Give the cohort a name." };
  }
  if (educationLevel && !EDUCATION_LEVELS.includes(educationLevel as never)) {
    return { error: "Choose a valid education level." };
  }

  // education_level decides which Job Zones an occupation match may draw from
  // (plan §8), so a typo here quietly changes every student's results.
  const intakeYear = intakeYearRaw ? Number(intakeYearRaw) : null;
  if (intakeYearRaw && (!Number.isInteger(intakeYear) || intakeYear! < 2000)) {
    return { error: "Intake year does not look right." };
  }

  const admin = createAdminClient();
  const { error } = await admin.from("cohorts").insert({
    organisation_id: session.organisationId, // from the session, never the form
    name,
    intake_year: intakeYear,
    education_level: educationLevel || null,
    created_by: session.userId,
  });

  if (error) {
    return { error: "Could not create the cohort." };
  }

  revalidatePath("/dash");
  return { ok: `Created ${name}.` };
}

export async function renameCohort(
  _state: ActionState,
  formData: FormData,
): Promise<ActionState> {
  const session = await requireRole("counsellor", "org_admin", "superadmin");

  const cohortId = String(formData.get("cohort_id") ?? "");
  const name = String(formData.get("name") ?? "").trim();

  if (!cohortId || !name) {
    return { error: "Give the cohort a name." };
  }

  const admin = createAdminClient();
  // The organisation_id filter is the authorisation check, not decoration: the
  // admin client can see every tenant, so without it a guessed cohort id from
  // another school would be renamed successfully.
  const { error } = await admin
    .from("cohorts")
    .update({ name })
    .eq("id", cohortId)
    .eq("organisation_id", session.organisationId);

  if (error) {
    return { error: "Could not rename the cohort." };
  }

  revalidatePath("/dash");
  return { ok: "Renamed." };
}

export async function updateOrganisation(
  _state: ActionState,
  formData: FormData,
): Promise<ActionState> {
  const session = await requireRole("org_admin", "superadmin");

  const name = String(formData.get("name") ?? "").trim();
  const brandHex = String(formData.get("brand_hex") ?? "").trim();

  if (!name) {
    return { error: "The institution needs a name." };
  }
  if (brandHex && !/^#[0-9a-fA-F]{6}$/.test(brandHex)) {
    return { error: "Brand colour must look like #1C6A61." };
  }

  const admin = createAdminClient();
  const { error } = await admin
    .from("organisations")
    .update({ name, ...(brandHex ? { brand_hex: brandHex } : {}) })
    .eq("id", session.organisationId);

  if (error) {
    return { error: "Could not save those settings." };
  }

  revalidatePath("/dash/settings");
  return { ok: "Saved." };
}
