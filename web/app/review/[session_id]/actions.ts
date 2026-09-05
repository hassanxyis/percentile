"use server";

import { revalidatePath } from "next/cache";

import { requireReviewer } from "@/lib/dal";
import { createAdminClient } from "@/lib/supabase/admin";

/**
 * The three review actions (plan.md §9.1, M8): save draft, confirm, send back.
 *
 * Same two rules as every other action file here. `requireReviewer()` first,
 * because a server action is reachable by direct POST and hiding a button is
 * not authorisation. Writes through the admin client, because 0002_rls.sql
 * grants authenticated users SELECT and nothing else.
 *
 * What replaces the RLS this bypasses is the same shape as elsewhere: **the
 * reviewer id comes from the session, never from the form.** A `reviewer_id`
 * field in a FormData would let one member sign off in another's name — and on
 * this screen that name goes onto a psychologist's clinical judgement about a
 * minor (R3, §16). `save_review()` re-checks the role and the organisation in
 * SQL, so neither layer is load-bearing alone.
 *
 * All three go through the one `save_review()` function rather than a sequence
 * of supabase-js calls, for the reason 0009 states: confirming is five writes
 * that must not come apart.
 */

export type ReviewActionState =
  | { status: "idle" }
  | { status: "error"; message: string }
  | { status: "saved"; message: string };

/** What the form posts for each direction row. */
type DirectionInput = {
  onet_soc_code: string;
  local_title: string;
  rationale: string;
};

const MAX_DIRECTIONS = 3;

/**
 * Pull the up-to-3 direction rows out of the form.
 *
 * A row counts as present only if it has a title. The form always renders three
 * slots — an empty one is a slot the psychologist did not use, not an error, so
 * it is dropped rather than rejected.
 *
 * `local_title` falls back to the picked occupation's title, submitted as a
 * companion hidden field: the psychologist picking "Registered Nurses" and
 * typing nothing means the catalogue title, not a blank line on the student's
 * report.
 */
function readDirections(formData: FormData): DirectionInput[] {
  const directions: DirectionInput[] = [];

  for (let index = 0; index < MAX_DIRECTIONS; index += 1) {
    const code = String(formData.get(`direction_${index}_code`) ?? "").trim();
    const typed = String(formData.get(`direction_${index}_title`) ?? "").trim();
    const fallback = String(formData.get(`direction_${index}_code_title`) ?? "").trim();
    const rationale = String(formData.get(`direction_${index}_rationale`) ?? "").trim();

    const title = typed || fallback;
    if (!title) {
      continue;
    }

    directions.push({ onet_soc_code: code, local_title: title, rationale });
  }

  return directions;
}

async function save(
  formData: FormData,
  status: "in_progress" | "confirmed" | "needs_more_info",
): Promise<ReviewActionState> {
  const session = await requireReviewer();

  const sessionId = String(formData.get("session_id") ?? "");
  if (!sessionId) {
    return { status: "error", message: "Which session is this for?" };
  }

  const notes = String(formData.get("interview_notes") ?? "").trim();
  const mode = String(formData.get("interview_mode") ?? "").trim();
  const directions = readDirections(formData);

  // Checked here as well as in SQL so the psychologist gets a sentence rather
  // than a Postgres exception — and checked in SQL as well as here because this
  // action is not the only possible caller.
  if (status === "confirmed" && directions.length === 0) {
    return {
      status: "error",
      message: "Add at least one career direction before confirming.",
    };
  }

  const admin = createAdminClient();

  const { error } = await admin.rpc("save_review", {
    p_session_id: sessionId,
    p_reviewer_id: session.userId, // from the session, never the form
    p_status: status,
    p_interview_mode: mode || null,
    p_interview_notes: notes || null,
    // Which flags the reviewer had in front of them when they decided. The
    // form submits the codes it rendered, so a later flags.py change cannot
    // rewrite what this reviewer actually saw.
    p_flags_reviewed: JSON.parse(String(formData.get("flags_reviewed") ?? "[]")),
    p_directions: directions,
  });

  if (error) {
    // The SQL messages name the rule they enforce ("at most 3 career
    // directions", "role counsellor may not review sessions"), but they are
    // written for a developer reading `jobs.last_error`, not for someone
    // mid-review. Log the real one, show a calm one.
    console.error("save_review failed", {
      sessionId,
      status,
      code: error.code,
      message: error.message,
    });
    return {
      status: "error",
      message: "That did not save. Nothing has been changed — please try again.",
    };
  }

  revalidatePath(`/review/${sessionId}`);
  revalidatePath("/review");

  return {
    status: "saved",
    message:
      status === "confirmed"
        ? "Confirmed. The student's report has been queued."
        : status === "needs_more_info"
          ? "Sent back for a follow-up conversation."
          : "Draft saved.",
  };
}

export async function saveDraft(
  _state: ReviewActionState,
  formData: FormData,
): Promise<ReviewActionState> {
  return save(formData, "in_progress");
}

/**
 * Sign-off. This is the R9 gate: `save_review()` enqueues `render_student` here
 * and nowhere else, and the database trigger refuses the report until this has
 * run.
 */
export async function confirmReview(
  _state: ReviewActionState,
  formData: FormData,
): Promise<ReviewActionState> {
  return save(formData, "confirmed");
}

/** §9.1's send-back: the psychologist wants another conversation first. */
export async function sendBack(
  _state: ReviewActionState,
  formData: FormData,
): Promise<ReviewActionState> {
  return save(formData, "needs_more_info");
}
