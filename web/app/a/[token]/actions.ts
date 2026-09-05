"use server";

import { revalidatePath } from "next/cache";

import { createAdminClient } from "@/lib/supabase/admin";

import { hashToken } from "./queries";

/**
 * Consent and submission (plan.md §13, M6).
 *
 * Every other `actions.ts` in this app opens with `requireRole(...)`. This one
 * cannot: students are never authenticated. The token in the route segment is
 * the credential, and it is resolved to a participant inside Postgres — these
 * functions pass `sha256(token)` to `start_assessment` / `submit_assessment`
 * and hold no participant id to pass instead.
 *
 * Both are navigations rather than hot-path writes, so they stay server actions.
 * The per-item write does not — see `answer/route.ts` for why.
 */

export type ConsentState = { status: "idle" } | { status: "error"; message: string };

export type SubmitState =
  | { status: "idle" }
  | { status: "error"; message: string }
  | { status: "done" };

/**
 * Record consent and open the session.
 *
 * Idempotent: `sessions` is unique on participant_id and `start_assessment`
 * upserts, so a double-tapped button or a refreshed page lands on the same row.
 */
export async function beginAssessment(
  _state: ConsentState,
  formData: FormData,
): Promise<ConsentState> {
  const token = String(formData.get("token") ?? "");
  if (!token) {
    return { status: "error", message: "This link is not complete. Check the link you were sent." };
  }

  const admin = createAdminClient();
  const { error } = await admin.rpc("start_assessment", {
    p_token_hash: hashToken(token),
    // Recorded for support ("it did not work on my phone"), not for analytics.
    p_user_agent: null,
  });

  if (error) {
    return {
      status: "error",
      message: "We could not start your assessment. Please try again in a moment.",
    };
  }

  revalidatePath(`/a/${token}`);
  return { status: "idle" };
}

/**
 * Finish the assessment.
 *
 * `submit_assessment` re-checks completeness and refuses a partial submission,
 * so the client cannot skip to the end: an incomplete session would queue a
 * `score_session` job that `score_interests` then raises on, which would surface
 * in M7 as a mystery failed job rather than here as a plain refusal.
 */
export async function submitAssessment(
  _state: SubmitState,
  formData: FormData,
): Promise<SubmitState> {
  const token = String(formData.get("token") ?? "");
  if (!token) {
    return { status: "error", message: "This link is not complete. Check the link you were sent." };
  }

  const admin = createAdminClient();
  const { error } = await admin.rpc("submit_assessment", {
    p_token_hash: hashToken(token),
  });

  if (error) {
    // The likeliest cause by far is an answer that never reached us — the
    // retry queue gave up, or the last write was in flight when the button was
    // pressed. Say what to do about it rather than reporting a database error.
    return {
      status: "error",
      message:
        "Some answers have not reached us yet. Check your connection, then try again — nothing you have answered is lost.",
    };
  }

  revalidatePath(`/a/${token}`);
  return { status: "done" };
}
