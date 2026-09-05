import "server-only";

import { createHash } from "node:crypto";

import { createAdminClient } from "@/lib/supabase/admin";
import { groupIntoModules, type TakerItem, type TakerModule } from "@/lib/taker";

/**
 * Reads for the student taker (plan.md §13, M6).
 *
 * Every other `queries.ts` in this app goes through the RLS-bound client and
 * starts with `verifySession()`. This one cannot, and the difference is the
 * whole design of the student side:
 *
 *   - **There is no session.** Students are never authenticated (0002_rls.sql).
 *     `lib/dal.ts` is for counsellors; reaching for it here would redirect a
 *     sixteen-year-old to a login page they have no account for.
 *   - **There is no RLS to bind to.** `participants`, `sessions` and `responses`
 *     have policies for `authenticated` only, so an anon client reads nothing.
 *     The admin client is the only way in, and this file is one of the places
 *     `check-service-role.sh` exists to keep honest.
 *
 * What replaces RLS is the token, and one rule about it: **the lookup is by
 * `sha256(token)` and nothing else.** No `participant_id`, no `session_id`, no
 * cohort filter from the request. `0002_rls.sql`'s header states it and
 * `0006_taker_flow.sql` enforces it at the SQL boundary; this file is the layer
 * where it would be easiest to quietly break, by "just" accepting an id in a
 * search param to save a query.
 */

/** The stored form of a token. Mirrors `mintToken` in the upload action. */
export function hashToken(token: string): string {
  return createHash("sha256").update(token).digest("hex");
}

export type TakerParticipant = {
  id: string;
  full_name: string;
  status: string;
  consent_at: string | null;
};

export type TakerSession = {
  id: string;
  started_at: string | null;
  submitted_at: string | null;
};

export type TakerOrganisation = {
  name: string;
  brand_hex: string | null;
};

export type TakerState = {
  participant: TakerParticipant;
  organisation: TakerOrganisation;
  /** Null until the student consents — `start_assessment` creates it. */
  session: TakerSession | null;
  modules: TakerModule[];
  /** Item ids with a recorded answer. The resume position derives from this (R1). */
  answeredItemIds: string[];
};

/**
 * Everything the taker page needs, or null if the token resolves to nobody.
 *
 * Null covers a mistyped link, a token from a re-imported roster, and someone
 * guessing — deliberately indistinguishable. The page turns all three into the
 * same 404, for the same reason `getCohort` does: telling them apart confirms
 * which tokens exist.
 */
export async function loadTaker(token: string): Promise<TakerState | null> {
  // Cheap rejection before touching the database. A minted token is 32 bytes
  // base64url, so anything wildly off is a scan, not a student.
  if (!token || token.length > 128) {
    return null;
  }

  const admin = createAdminClient();

  const { data: participant } = await admin
    .from("participants")
    .select("id, full_name, status, consent_at, cohorts(organisations(name, brand_hex))")
    .eq("invite_token_hash", hashToken(token))
    .maybeSingle<
      TakerParticipant & {
        cohorts: { organisations: TakerOrganisation | null } | null;
      }
    >();

  if (!participant) {
    return null;
  }

  const { data: session } = await admin
    .from("sessions")
    .select("id, started_at, submitted_at")
    .eq("participant_id", participant.id)
    .maybeSingle<TakerSession>();

  // Ordered here as well as in `groupIntoModules`, because the ordering the
  // student sees should not depend on what Postgres felt like returning.
  const { data: items } = await admin
    .from("items")
    .select("id, instrument_code, ordinal, code, text, response_min, response_max")
    .order("instrument_code")
    .order("ordinal")
    .returns<TakerItem[]>();

  // Only once a session exists. Asking for responses to a session that has not
  // been created is not an error state, just an empty one.
  let answeredItemIds: string[] = [];
  if (session) {
    const { data: responses } = await admin
      .from("responses")
      .select("item_id")
      .eq("session_id", session.id)
      .returns<{ item_id: string }[]>();

    answeredItemIds = (responses ?? []).map((r) => r.item_id);
  }

  return {
    participant: {
      id: participant.id,
      full_name: participant.full_name,
      status: participant.status,
      consent_at: participant.consent_at,
    },
    organisation: participant.cohorts?.organisations ?? {
      name: "your institution",
      brand_hex: null,
    },
    session,
    // Empty when no instrument has been loaded (R2 — all code must work with an
    // empty item table). The page shows a "not ready yet" screen rather than an
    // assessment with no questions in it.
    modules: groupIntoModules(items ?? []),
    answeredItemIds,
  };
}
