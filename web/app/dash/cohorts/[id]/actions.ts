"use server";

import { revalidatePath } from "next/cache";

import { canResendInvite } from "@/lib/roster";
import { requireRole } from "@/lib/dal";
import { createAdminClient } from "@/lib/supabase/admin";
import type { ActionState } from "../../actions";

/**
 * Roster actions — resend an invite, download a report (plan §13, M10).
 *
 * Follows the rules in `app/dash/actions.ts`: `requireRole` first, the admin
 * client for writes, and the tenant boundary re-checked in the query rather
 * than trusted from the form. A server action is reachable by direct POST, so
 * every id arriving here is attacker-controlled — the `organisation_id` filter
 * on each lookup is the authorisation check, not decoration.
 */

/** How long a download link lives. Long enough to click, short enough to expire
 *  before a shared school computer's history matters. */
const DOWNLOAD_URL_SECONDS = 300;

const REPORT_BUCKET = "reports";

/**
 * Queue a fresh invite email for one student.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * THIS DOES NOT SEND, AND IT NEVER SEES A TOKEN.
 *
 * It enqueues a `send_email` job and returns. The engine's handler mints the
 * token at send time and writes only its hash
 * (`engine/app/jobs/handlers/email.py`), so the token exists for the duration
 * of one HTTP request in another process and is never stored, logged, or
 * returned to this browser.
 *
 * The consequence a counsellor must understand, and the reason the confirmation
 * message says it: the student's PREVIOUS link stops working. Only
 * `sha256(token)` is kept, so a resend replaces rather than re-sends.
 * ─────────────────────────────────────────────────────────────────────────────
 */
export async function resendInvite(
  _state: ActionState,
  formData: FormData,
): Promise<ActionState> {
  const session = await requireRole("counsellor", "org_admin", "superadmin");

  const participantId = String(formData.get("participant_id") ?? "");
  if (!participantId) {
    return { error: "No student selected." };
  }

  const admin = createAdminClient();

  // The admin client bypasses RLS, so this join IS the tenant check. Without
  // the organisation filter, a guessed participant id from another school would
  // resend successfully — and re-mint that student's token, breaking their link.
  const { data: participant } = await admin
    .from("participants")
    .select("id, full_name, email, status, cohort_id, cohorts!inner(organisation_id)")
    .eq("id", participantId)
    .eq("cohorts.organisation_id", session.organisationId)
    .maybeSingle<{
      id: string;
      full_name: string;
      email: string | null;
      status: string;
      cohort_id: string;
    }>();

  // No row means no such student, or another school's. Both give the same
  // message: distinguishing them would confirm the id exists somewhere.
  if (!participant) {
    return { error: "Could not find that student." };
  }

  if (!participant.email) {
    return { error: `${participant.full_name} has no email address on the roster.` };
  }

  // Mirrors `_send_invite`'s guard. Both are needed: this one gives a counsellor
  // a sentence they can act on, that one is the enforcement. Re-inviting a
  // finished student would mint a token that reopens a closed assessment —
  // `start_assessment` refuses it, so the link would appear to work and then fail.
  if (!canResendInvite(participant.status)) {
    return {
      error: `${participant.full_name} has already submitted, so a new link would not work.`,
    };
  }

  // Deliberately NOT deduped against a pending invite job. A counsellor pressing
  // this twice means "it did not arrive" — usually true, and the second send is
  // the point. Every send re-mints, so two jobs produce one working link, which
  // is the same outcome as one job.
  const { error } = await admin.from("jobs").insert({
    kind: "send_email",
    payload: { template: "invite", participant_id: participant.id },
  });

  if (error) {
    return { error: "Could not queue that invitation." };
  }

  await admin.from("audit_log").insert({
    actor: session.userId,
    action: "invite.resent",
    subject: participant.id,
    meta: { cohort_id: participant.cohort_id },
  });

  revalidatePath(`/dash/cohorts/${participant.cohort_id}`);

  // "Queued", not "Sent". `POST /tick` runs every five minutes, so the mail has
  // not left yet — and a counsellor told "Sent" who watches a student's inbox
  // for thirty seconds will press the button again.
  return {
    ok:
      `A new link is queued for ${participant.full_name}. ` +
      `Their previous link has stopped working.`,
  };
}

/**
 * A short-lived URL for one student's report.
 *
 * Returns the URL rather than redirecting, so the caller can open it in a new
 * tab and keep the roster on screen. It is never rendered into the page's HTML
 * — the client component requests it on click, which keeps signed URLs out of
 * the document, out of view-source, and out of any screenshot of this screen.
 *
 * Five minutes, not the seven days the student's own emailed link gets (§15).
 * Different threat: this one is minted on a school computer that a counsellor
 * may walk away from, and it is one click from being in the browser history of
 * a shared machine.
 */
export async function reportDownloadUrl(
  reportId: string,
): Promise<{ url?: string; error?: string }> {
  const session = await requireRole("counsellor", "org_admin", "superadmin");

  if (!reportId) {
    return { error: "No report selected." };
  }

  const admin = createAdminClient();

  // Same shape as above: the admin client sees every tenant, so the
  // organisation filter is what stops a guessed report id from another school
  // returning a signed URL to a stranger's psychological profile.
  const { data: report } = await admin
    .from("reports")
    .select(
      "id, storage_path, kind, " +
        "sessions!inner(participants!inner(cohorts!inner(organisation_id)))",
    )
    .eq("id", reportId)
    .eq("kind", "student")
    .eq(
      "sessions.participants.cohorts.organisation_id",
      session.organisationId,
    )
    .maybeSingle<{ id: string; storage_path: string; kind: string }>();

  if (!report) {
    return { error: "Could not find that report." };
  }

  const { data: signed, error } = await admin.storage
    .from(REPORT_BUCKET)
    .createSignedUrl(report.storage_path, DOWNLOAD_URL_SECONDS);

  if (error || !signed?.signedUrl) {
    // The usual cause is that the object is missing — a `reports` row whose
    // upload failed. Not surfaced as "no report": the counsellor can see one
    // listed, and "try again" is wrong advice if the file is genuinely gone.
    return { error: "That report could not be opened. It may still be rendering." };
  }

  await admin.from("audit_log").insert({
    actor: session.userId,
    action: "report.downloaded",
    subject: report.id,
    meta: { role: session.role },
  });

  return { url: signed.signedUrl };
}
