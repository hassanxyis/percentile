import Link from "next/link";
import { notFound } from "next/navigation";

import { requireReviewer } from "@/lib/dal";
import { describeIntendedField } from "@/lib/intended-fields";
import {
  interestBandFlag,
  REVIEW_STATUS_LABEL,
  waitingFor,
  warnCount,
} from "@/lib/review";

import {
  FlagsSection,
  Get2Section,
  InterestSection,
  MatchesSection,
  PersonalitySection,
} from "./profile";
import { listOccupations, loadReview } from "./queries";
import { ReviewForm } from "./review-form";

/**
 * /review/[session_id] — one student's full review screen (plan.md §9.1, M8).
 *
 * The human checkpoint R9 requires. Everything the engine produced is on the
 * left; the psychologist's own judgement goes in on the right, and confirming
 * is what releases the student's report.
 *
 * Two things this screen deliberately does not do:
 *
 *   - **It does not suggest.** R3 reserves interpretation for a person, and
 *     §9.1 says the notes are never model-generated or auto-filled from the
 *     scores. Nothing here pre-fills a direction or drafts a rationale.
 *   - **It does not show percentiles.** R4 forbids them until local norms reach
 *     n >= 300 for the population. Raw scores and provisional bands only.
 */
export default async function ReviewDetailPage({
  params,
}: PageProps<"/review/[session_id]">) {
  const { session_id } = await params;
  await requireReviewer();

  const detail = await loadReview(session_id);
  // Null means RLS returned nothing: no such session, or another organisation's.
  // Both are a 404, for the same reason `getCohort` does it — telling them apart
  // confirms which sessions exist.
  if (!detail) {
    notFound();
  }

  const occupations = await listOccupations();

  // §9.1 names `undifferentiated` beside `straightlining` as the pair a reviewer
  // must not miss, but it is the interest *band* rather than a flags.py flag —
  // so it is lifted into the same list here.
  const bandFlag = interestBandFlag(detail.interests?.band);
  const flags = bandFlag ? [bandFlag, ...detail.flags] : detail.flags;
  const warns = warnCount(flags);

  const confirmed = detail.review?.status === "confirmed";

  return (
    <main className="mx-auto flex w-full max-w-6xl flex-col gap-8 px-6 py-12">
      <header className="flex flex-col gap-3">
        <Link href="/review" className="text-sm underline">
          ← Review queue
        </Link>

        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">
              {detail.participant.full_name}
            </h1>
            <p className="text-sm text-black/60 dark:text-white/60">
              {detail.cohort_name} · intending{" "}
              {describeIntendedField(detail.participant.intended_field)} · submitted{" "}
              {waitingFor(detail.submitted_at, new Date())} ago
            </p>
          </div>

          {detail.review && (
            <span className="rounded border border-black/15 px-3 py-1 text-xs dark:border-white/20">
              {REVIEW_STATUS_LABEL[detail.review.status] ?? detail.review.status}
            </span>
          )}
        </div>

        {/* §7.4: two or more warns exclude a participant from cohort
            aggregates, and the cohort report must then say how many were
            excluded and why. The reviewer is the person who can act on it. */}
        {warns >= 2 && (
          <p
            role="alert"
            className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm"
          >
            {warns} response-quality warnings. This session is excluded from
            cohort averages, and the answers are worth discussing before you
            confirm.
          </p>
        )}

        {detail.participant.status === "needs_more_info" && (
          <p className="rounded border border-black/15 px-3 py-2 text-sm dark:border-white/20">
            You sent this back for a follow-up conversation. Confirming will
            release the report.
          </p>
        )}
      </header>

      <div className="grid gap-10 lg:grid-cols-[3fr_2fr]">
        {/* The engine's output. Flags first: §9.1 wants them prominent, not
            buried under the charts. */}
        <div className="flex flex-col gap-8">
          <FlagsSection flags={flags} />
          <InterestSection interests={detail.interests} />
          <PersonalitySection personality={detail.personality} />
          <Get2Section get2={detail.get2} />
          <MatchesSection matches={detail.matches} />

          {/* R6: O*NET attribution is a licence term, on every page that shows
              its data. */}
          <p className="border-t border-black/10 pt-4 text-xs text-black/50 dark:border-white/10 dark:text-white/50">
            Occupation data incorporates information from the O*NET Database by
            the U.S. Department of Labor, Employment and Training Administration
            (USDOL/ETA). O*NET® is a trademark of USDOL/ETA.
            {detail.engine_version && ` Scored by engine ${detail.engine_version}.`}
          </p>
        </div>

        <div className="lg:sticky lg:top-8 lg:self-start">
          <ReviewForm
            sessionId={detail.session_id}
            occupations={occupations}
            flagCodes={flags.map((flag) => flag.code)}
            existing={detail.review}
            directions={detail.directions}
            confirmed={confirmed}
          />
        </div>
      </div>
    </main>
  );
}
