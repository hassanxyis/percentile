"use client";

import { useActionState } from "react";

import { beginAssessment, type ConsentState } from "./actions";

/**
 * Consent (plan.md §13, §16, M6).
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * TODO(plan.md §20 item 8) — THE WORDING BELOW IS A DRAFT AND NEEDS A HUMAN.
 *
 * §20 item 8 is explicit: the guardian-consent wording, updated for the review
 * disclosure, "should be reviewed by someone at GIFT or by your two counsellor
 * contacts, not drafted solo." Most participants are under 18 (§16). This draft
 * exists so the flow is testable end to end; it is not the text to put in front
 * of a real cohort, and the pilot should not run on it unreviewed.
 *
 * What the reviewer needs to check, beyond tone:
 *   - Does it disclose the psychologist review step plainly enough (§13)?
 *   - Is it free of anything that reads as therapy, counselling for distress,
 *     or a clinical service (R7)?
 *   - Is student-facing assent enough here, or does a guardian have to consent
 *     separately and out of band before the link is sent at all?
 * ─────────────────────────────────────────────────────────────────────────────
 *
 * The screen deliberately does not have a "decline" button. A student who does
 * not want to take part closes the page; a button that records a refusal would
 * put a decision about a minor into a roster their school reads, which is a
 * conversation to have with a person, not a click to log.
 */

export function ConsentScreen({
  token,
  firstName,
  organisationName,
  itemCount,
  moduleCount,
}: {
  token: string;
  firstName: string;
  organisationName: string;
  itemCount: number;
  moduleCount: number;
}) {
  const [state, formAction, pending] = useActionState<ConsentState, FormData>(
    beginAssessment,
    { status: "idle" },
  );

  return (
    <main className="mx-auto flex w-full max-w-xl flex-col gap-6 px-6 py-12">
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">
          Hello {firstName}
        </h1>
        <p className="text-black/70 dark:text-white/70">
          {organisationName} has asked you to complete this before you talk
          about your subject and career choices.
        </p>
      </header>

      <div className="flex flex-col gap-4 text-sm leading-relaxed">
        <section className="flex flex-col gap-2">
          <h2 className="font-medium">What this is</h2>
          <p className="text-black/70 dark:text-white/70">
            {moduleCount === 1
              ? "A set of questions"
              : `${moduleCount} short sets of questions`}{" "}
            — {itemCount} in all, about twenty minutes. The first set asks which
            activities you would enjoy. The second asks how you tend to go about
            things. There are no right answers and nothing to revise for.
          </p>
          {/* R7. This is a career instrument and the copy must never imply
              otherwise — not in the report, not in marketing, not here. */}
          <p className="text-black/70 dark:text-white/70">
            This is not a test of ability or intelligence, and it is not a
            health check of any kind. Nobody passes or fails it.
          </p>
        </section>

        <section className="flex flex-col gap-2">
          <h2 className="font-medium">What happens next</h2>
          {/* The review disclosure §13 requires, in the plainest words that
              still say what actually happens. */}
          <p className="text-black/70 dark:text-white/70">
            Your answers do not turn straight into a report. A trained
            counsellor at {organisationName} reads through your results first,
            and they may want a short conversation with you before your report
            is finished. That is a normal part of this — it is how the results
            end up meaning something for you rather than being a printout.
          </p>
        </section>

        <section className="flex flex-col gap-2">
          <h2 className="font-medium">Who sees your results</h2>
          <p className="text-black/70 dark:text-white/70">
            You do, and so does the counsellor at {organisationName} who is
            guiding you. Your school also sees whole-class summaries, which
            never single you out. Nobody else is given your results.
          </p>
          <p className="text-black/70 dark:text-white/70">
            Answer honestly rather than the way you think you are supposed to.
            An answer chosen to look good only makes the report less useful to
            you.
          </p>
        </section>
      </div>

      <form action={formAction} className="flex flex-col gap-3">
        <input type="hidden" name="token" value={token} />

        {state.status === "error" && (
          <p role="alert" className="text-sm text-red-600 dark:text-red-400">
            {state.message}
          </p>
        )}

        <button
          type="submit"
          disabled={pending}
          className="self-start rounded bg-black px-5 py-3 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-black"
        >
          {pending ? "Starting…" : "I understand — start"}
        </button>
        <p className="text-xs text-black/50 dark:text-white/50">
          You can stop at any point and come back to this same link. Your
          answers are kept as you go.
        </p>
      </form>
    </main>
  );
}
