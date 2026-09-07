import { describe, expect, it } from "vitest";

import {
  canResendInvite,
  isOverdue,
  progressSentence,
  REVIEW_BACKLOG_DAYS,
  statusLabel,
  summarise,
  waitingFor,
} from "@/lib/roster";

/**
 * The counsellor dashboard's display logic (M10).
 *
 * Two tests here carry real weight rather than checking arithmetic:
 *
 * * `statusLabel` must cover every value the database allows, because a missing
 *   entry renders a raw enum beside a student's name — and one of those values
 *   is `failed`, which a counsellor would read as a verdict on the student.
 * * `canResendInvite` must refuse a submitted student, because a resend mints a
 *   fresh token and hands them a link into a closed assessment.
 */

const NOW = new Date("2026-03-20T12:00:00Z");

function daysAgo(days: number): string {
  return new Date(NOW.getTime() - days * 24 * 3_600_000).toISOString();
}

describe("statusLabel", () => {
  /**
   * Every value `participants_status_check` permits (0003_reviews.sql). A
   * literal list rather than an import: the point is to notice when the two
   * drift, and a new status should fail here until someone writes the words a
   * counsellor reads.
   */
  const EVERY_STATUS = [
    "invited",
    "started",
    "submitted",
    "scored",
    "pending_review",
    "reviewed",
    "needs_more_info",
    "confirmed",
    "failed",
  ];

  it.each(EVERY_STATUS)("has a plain-language label for %s", (status) => {
    const label = statusLabel(status);
    expect(label).toBeTruthy();
    expect(label).not.toBe(status);
    // No underscores: the presence of one means a raw enum reached the screen.
    expect(label).not.toContain("_");
  });

  it("does not describe a follow-up as a rejection (R7)", () => {
    // The psychologist asked for another conversation. That is ordinary
    // practice, not a problem with the student.
    const label = statusLabel("needs_more_info").toLowerCase();
    for (const word of ["reject", "fail", "problem", "incomplete", "issue"]) {
      expect(label).not.toContain(word);
    }
  });

  it("does not blame the student for an engine failure", () => {
    // `failed` means a job broke. "Failed" beside a sixteen-year-old's name
    // reads as something they did.
    expect(statusLabel("failed").toLowerCase()).not.toBe("failed");
  });

  it("shows an unknown status raw rather than blank", () => {
    // A blank cell reads as "nothing here", which is the opposite of "something
    // we did not expect".
    expect(statusLabel("teleported")).toBe("teleported");
  });
});

describe("canResendInvite", () => {
  it("allows a student who has not finished", () => {
    expect(canResendInvite("invited")).toBe(true);
    expect(canResendInvite("started")).toBe(true);
  });

  /**
   * The guard that matters. A resend mints a FRESH token at send time
   * (engine/app/jobs/handlers/email.py), so re-inviting a finished student
   * hands them a working link into a closed assessment — `start_assessment`
   * then refuses it, which is a link that appears to work and then fails.
   */
  it.each(["submitted", "scored", "pending_review", "reviewed", "confirmed"])(
    "refuses a student at %s",
    (status) => {
      expect(canResendInvite(status)).toBe(false);
    },
  );

  it("refuses a follow-up, because they already submitted once", () => {
    expect(canResendInvite("needs_more_info")).toBe(false);
  });
});

describe("isOverdue", () => {
  it("flags a session waiting longer than the backlog window (§9.4)", () => {
    expect(isOverdue("pending_review", daysAgo(REVIEW_BACKLOG_DAYS + 1), NOW)).toBe(true);
  });

  it("does not flag one inside the window", () => {
    expect(isOverdue("pending_review", daysAgo(2), NOW)).toBe(false);
  });

  it("does not flag a student who has not submitted", () => {
    expect(isOverdue("invited", null, NOW)).toBe(false);
    expect(isOverdue("started", null, NOW)).toBe(false);
  });

  it("does not flag a completed session however old", () => {
    expect(isOverdue("confirmed", daysAgo(90), NOW)).toBe(false);
  });

  it("does not flag a follow-up: the psychologist already acted", () => {
    // The wait is now on a conversation between two people, not on the queue.
    expect(isOverdue("needs_more_info", daysAgo(30), NOW)).toBe(false);
  });

  it("survives an unparseable timestamp", () => {
    expect(isOverdue("pending_review", "not a date", NOW)).toBe(false);
  });
});

describe("waitingFor", () => {
  it("reads coarsely", () => {
    expect(waitingFor(daysAgo(3), NOW)).toBe("3 days");
    expect(waitingFor(daysAgo(1), NOW)).toBe("1 day");
    expect(waitingFor(new Date(NOW.getTime() - 4 * 3_600_000).toISOString(), NOW)).toBe(
      "4 hours",
    );
    expect(waitingFor(new Date(NOW.getTime() - 60_000).toISOString(), NOW)).toBe("just now");
  });

  it("clamps a future timestamp rather than saying 'in 1 hour'", () => {
    const future = new Date(NOW.getTime() + 3_600_000).toISOString();
    expect(waitingFor(future, NOW)).toBe("just now");
  });

  it("has nothing to say about a student who has not submitted", () => {
    expect(waitingFor(null, NOW)).toBe("—");
  });
});

describe("summarise", () => {
  const roster = [
    { status: "invited", submitted_at: null },
    { status: "invited", submitted_at: null },
    { status: "started", submitted_at: null },
    { status: "pending_review", submitted_at: daysAgo(1) },
    { status: "pending_review", submitted_at: daysAgo(9) }, // overdue
    { status: "reviewed", submitted_at: daysAgo(2) },
    { status: "needs_more_info", submitted_at: daysAgo(20) },
    { status: "confirmed", submitted_at: daysAgo(30) },
    { status: "failed", submitted_at: daysAgo(4) },
  ];

  it("buckets the roster", () => {
    const progress = summarise(roster, NOW);

    expect(progress.total).toBe(9);
    expect(progress.notStarted).toBe(2);
    expect(progress.inProgress).toBe(1);
    expect(progress.awaitingReview).toBe(3); // 2 pending_review + 1 reviewed
    expect(progress.complete).toBe(1);
    expect(progress.needsAttention).toBe(1);
    expect(progress.overdue).toBe(1);
  });

  it("counts a follow-up as neither waiting nor complete", () => {
    // It has its own label in the table. Folding it into a bucket would
    // misdescribe it in both directions.
    const progress = summarise([{ status: "needs_more_info", submitted_at: daysAgo(20) }], NOW);

    expect(progress.awaitingReview).toBe(0);
    expect(progress.complete).toBe(0);
    expect(progress.total).toBe(1);
  });

  it("handles an empty cohort", () => {
    const progress = summarise([], NOW);
    expect(progress.total).toBe(0);
    expect(progress.overdue).toBe(0);
  });
});

describe("progressSentence", () => {
  /**
   * plan §9's statistical honesty rule: never print a percentage on a
   * denominator below 10 without the denominator beside it. A pilot cohort is
   * 15–25 students, and "60% complete" out of 5 is three people.
   */
  it("states a count with its total, never a bare percentage", () => {
    const sentence = progressSentence(summarise(
      [
        { status: "confirmed", submitted_at: null },
        { status: "invited", submitted_at: null },
        { status: "invited", submitted_at: null },
      ],
      NOW,
    ));

    expect(sentence).toBe("1 of 3 complete");
    expect(sentence).not.toContain("%");
  });

  it("says so plainly when the cohort is empty", () => {
    expect(progressSentence(summarise([], NOW))).toBe("No students yet.");
  });
});
