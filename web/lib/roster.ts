/**
 * Roster progress logic (plan.md §10, §13, M10).
 *
 * Pure: participant rows in, display decisions out. No I/O, no Supabase, no
 * Next imports — the same shape as `review.ts` and `roster-csv.ts`, for the same
 * reason. What lives here is what a test can pin down without a database: the
 * status vocabulary a counsellor reads, and the arithmetic behind the progress
 * summary.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * WHAT A COUNSELLOR MAY SEE, AND WHY IT STOPS WHERE IT DOES
 *
 * A counsellor has NO read policy on `reviews` (0003_reviews.sql). Not a
 * narrowed one — none at all. `interview_notes` is the psychologist's
 * clinical-adjacent working notes, and §16 says to treat it like health data;
 * RLS is row-scoped rather than column-scoped, so any SELECT policy on that
 * table would hand a matching counsellor every column including those notes.
 *
 * The only way through is `review_progress_for_session()`, a SECURITY DEFINER
 * function returning exactly two columns: status and confirmed_at.
 *
 * So this module knows a session is waiting, or being reviewed, or needs a
 * follow-up. It never knows why, and there is deliberately no field for it.
 * "Follow-up needed" is the whole message a counsellor gets, and the
 * conversation that follows happens between two humans.
 * ─────────────────────────────────────────────────────────────────────────────
 */

/** `participants.status`, as 0003_reviews.sql constrains it. */
export type ParticipantStatus =
  | "invited"
  | "started"
  | "submitted"
  | "scored"
  | "pending_review"
  | "reviewed"
  | "needs_more_info"
  | "confirmed"
  | "failed";

/**
 * Plain-language status. §5's vocabulary is for the system, not for a reader.
 *
 * Three of these are worded carefully rather than translated:
 *
 * * `scored` and `pending_review` both read "Awaiting review". A counsellor has
 *   no use for the distinction — one is a row the engine finished writing a
 *   second before the other — and showing two labels for one state invites the
 *   question "what's the difference?", which has no useful answer.
 * * `needs_more_info` is "Follow-up needed", never "rejected" or "failed". The
 *   psychologist asked for another conversation; that is ordinary practice, not
 *   a problem with the student (R7 forbids clinical framing anywhere in the UI).
 * * `failed` is "Needs attention" rather than "Failed". It means a job broke —
 *   an engine problem, not something the student did — and a counsellor reading
 *   "Failed" beside a sixteen-year-old's name will reasonably assume otherwise.
 */
export const STATUS_LABEL: Record<string, string> = {
  invited: "Invited",
  started: "In progress",
  submitted: "Submitted",
  scored: "Awaiting review",
  pending_review: "Awaiting review",
  reviewed: "Being reviewed",
  needs_more_info: "Follow-up needed",
  confirmed: "Complete",
  failed: "Needs attention",
};

export function statusLabel(status: string): string {
  // An unknown status is a value the database gained without this file. Show it
  // raw rather than blank: a blank cell reads as "nothing here", which is the
  // opposite of "something we did not expect".
  return STATUS_LABEL[status] ?? status;
}

/** Statuses where the student still has work to do — a resend can help. */
export const RESENDABLE: readonly string[] = ["invited", "started"] as const;

/**
 * Whether re-sending an invite is allowed for this student.
 *
 * Refused once they have submitted. The invite handler mints a FRESH token at
 * send time (`engine/app/jobs/handlers/email.py`), so a resend to a student who
 * finished would hand them a working link into a closed assessment —
 * `start_assessment` then refuses it, which is a link that appears to work and
 * then fails confusingly.
 *
 * Mirrors the guard in `_send_invite`. Both are needed: this one decides
 * whether to render a button, and that one is the enforcement.
 */
export function canResendInvite(status: string): boolean {
  return RESENDABLE.includes(status);
}

/** Sessions waiting longer than this are overdue (§9.4's five-day nudge). */
export const REVIEW_BACKLOG_DAYS = 5;

/** Statuses that mean "submitted, and a human has not signed it off yet". */
const AWAITING_REVIEW: readonly string[] = [
  "submitted",
  "scored",
  "pending_review",
  "reviewed",
];

/**
 * Whether this student has been waiting on a review too long (§9.4).
 *
 * The counsellor's view of the same five days that trigger the engine's backlog
 * email. Shown here because §9.4 calls a stalled queue "an operational problem
 * to fix" — and the person who can chase a psychologist is looking at this
 * screen, not at the alert mailbox.
 *
 * `needs_more_info` is deliberately excluded: the psychologist has acted, and
 * the wait is now on a conversation rather than on the queue.
 */
export function isAwaitingReview(status: string): boolean {
  return AWAITING_REVIEW.includes(status);
}

export function isOverdue(
  status: string,
  submittedAt: string | null,
  now: Date,
): boolean {
  if (!isAwaitingReview(status) || !submittedAt) {
    return false;
  }
  const ms = now.getTime() - new Date(submittedAt).getTime();
  if (Number.isNaN(ms)) {
    return false;
  }
  return ms >= REVIEW_BACKLOG_DAYS * 24 * 3_600_000;
}

/**
 * "3 days" / "4 hours" / "just now" — how long a student has been waiting.
 *
 * Coarse on purpose, matching `review.ts`'s version. Nobody needs minutes, and
 * a precise figure invites reading the roster as a performance metric rather
 * than a workload.
 */
export function waitingFor(submittedAt: string | null, now: Date): string {
  if (!submittedAt) {
    return "—";
  }

  const ms = now.getTime() - new Date(submittedAt).getTime();
  if (Number.isNaN(ms)) {
    return "—";
  }
  // Clock skew between the database and the renderer can put a just-written row
  // slightly in the future. "in 1 hour" beside a waiting student is absurd.
  if (ms < 0) {
    return "just now";
  }

  const hours = Math.floor(ms / 3_600_000);
  if (hours < 1) return "just now";
  if (hours < 24) return `${hours} ${hours === 1 ? "hour" : "hours"}`;

  const days = Math.floor(hours / 24);
  return `${days} ${days === 1 ? "day" : "days"}`;
}

/**
 * Cohort progress, for the summary line (§10).
 *
 * Four buckets rather than nine statuses. A counsellor's questions are "who
 * hasn't started", "who am I waiting on", and "who is done" — the state machine
 * behind those is the engine's business.
 */
export type Progress = {
  total: number;
  notStarted: number;
  inProgress: number;
  awaitingReview: number;
  complete: number;
  needsAttention: number;
  overdue: number;
};

export function summarise(
  participants: { status: string; submitted_at: string | null }[],
  now: Date,
): Progress {
  const progress: Progress = {
    total: participants.length,
    notStarted: 0,
    inProgress: 0,
    awaitingReview: 0,
    complete: 0,
    needsAttention: 0,
    overdue: 0,
  };

  for (const participant of participants) {
    const { status } = participant;

    if (status === "invited") progress.notStarted += 1;
    else if (status === "started") progress.inProgress += 1;
    else if (status === "confirmed") progress.complete += 1;
    else if (status === "failed") progress.needsAttention += 1;
    // `needs_more_info` counts as neither waiting nor done: the psychologist has
    // acted and the ball is with a conversation. It is visible in the table by
    // its own label rather than being folded into a bucket that misdescribes it.
    else if (isAwaitingReview(status)) progress.awaitingReview += 1;

    if (isOverdue(status, participant.submitted_at, now)) {
      progress.overdue += 1;
    }
  }

  return progress;
}

/**
 * "12 of 20 complete" — the summary sentence.
 *
 * Never a bare percentage. plan §9's statistical honesty rule: never print a
 * percentage on a denominator below 10 without the denominator beside it, and a
 * pilot cohort is 15–25 students. A count with its total is honest at any size.
 */
export function progressSentence(progress: Progress): string {
  if (progress.total === 0) {
    return "No students yet.";
  }
  return `${progress.complete} of ${progress.total} complete`;
}
