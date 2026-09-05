/**
 * Review-screen logic (plan.md §9, M8).
 *
 * Pure: scores and flags in, display decisions out. No I/O, no Supabase, no
 * Next imports — the same shape as `roster-csv.ts` and `taker.ts`, for the same
 * reason. What is here is the part worth pinning down with a test: the flag
 * vocabulary a psychologist reads, and the arithmetic behind the charts.
 *
 * Nothing in this file interprets a result. R3 reserves interpretation for
 * `engine/app/content/interpretations.yaml`, written by a person with
 * psychology training — and §9.1 is explicit that the psychologist's own notes
 * are never model-generated or auto-filled from the scores. The strings below
 * describe what the engine measured ("12 identical consecutive answers"), never
 * what it means about the student.
 */

/** One entry of `scores.flags`, as `flags.py` writes it. */
export type QualityFlag = {
  code: string;
  severity: string;
  detail: string;
};

/**
 * Plain-language meaning for each flag `flags.py` can emit (§9.1: "every
 * quality flag from §7.4 with its plain-language meaning").
 *
 * Phrased as what to do about it, because that is what the screen is for. A
 * flag is a signal to talk to the student, never a verdict on them — the
 * wording here follows `flags.py`'s own instruction that a reviewer may read
 * one of these out loud.
 */
const FLAG_MEANINGS: Record<string, string> = {
  straightlining:
    "The same answer was chosen many times in a row. Often means fatigue or " +
    "skimming rather than a considered response — worth asking how they found " +
    "the questionnaire.",
  too_fast:
    "The assessment was completed faster than the items can reasonably be read. " +
    "The scores below may not reflect considered answers.",
  inconsistent_pairs:
    "Questions asking the same thing in opposite directions got answers that do " +
    "not agree. Can mean a misread item, or a trait the student genuinely sees " +
    "both ways depending on context.",
  long_gap:
    "The assessment was started and finished days apart, so the two halves may " +
    "reflect different moods or circumstances.",
  get2_uniform_response:
    "Every entrepreneurial subscale landed within a few points of each other, " +
    "which usually means the section was clicked through rather than a genuinely " +
    "flat profile.",
};

export function flagMeaning(code: string): string {
  // An unknown code is a flag added to flags.py without a line here. Say so
  // plainly rather than rendering an empty cell — a blank space beside a warning
  // reads as "nothing to see", which is the opposite of true.
  return (
    FLAG_MEANINGS[code] ??
    "This check was raised by the scoring engine. See the detail beside it."
  );
}

/**
 * Flags that change what the review conversation must cover (§9.1: these
 * "should be visually distinct from `info`-level flags").
 *
 * `undifferentiated` is not a `flags.py` flag — it is the interest
 * `band`, surfaced as a pseudo-flag by `interestBandFlag` below, because §9.1
 * names it alongside `straightlining` as the pair a reviewer must not miss.
 */
export function isProminentFlag(flag: QualityFlag): boolean {
  return flag.severity === "warn" || flag.code === "undifferentiated";
}

/** Two or more warns exclude a participant from cohort aggregates (§7.4). */
export function warnCount(flags: QualityFlag[]): number {
  return flags.filter((flag) => flag.severity === "warn").length;
}

/**
 * The interest band as a flag, when it is one.
 *
 * `score_interests` returns `band: "undifferentiated"` when no clear Holland
 * code emerged, and §7.1 says the report "must say this in words and tell the
 * counsellor to explore rather than recommend". §9.1 wants it visually
 * distinct on this screen. It is not in `scores.flags`, so it is lifted here.
 *
 * Returns null for any other band — an absent flag, not a reassuring one.
 */
export function interestBandFlag(band: string | null | undefined): QualityFlag | null {
  if (band !== "undifferentiated") {
    return null;
  }
  return {
    code: "undifferentiated",
    severity: "warn",
    detail: "no clear interest profile emerged",
  };
}

/**
 * Points of a regular hexagon for the RIASEC chart, clockwise from the top.
 *
 * Hand-computed rather than pulled from a charting library, matching
 * `engine/app/report/charts.py` — the output is a hexagon and some bars, and
 * the review screen should show the same shape the student's PDF will.
 *
 * `max` is the instrument's ceiling, not the observed maximum: scaling to the
 * observed value would make every profile look equally strong, which is exactly
 * the elevation-vs-shape distinction §8 ipsatizes away for matching.
 */
export function hexagonPoints(
  values: number[],
  max: number,
  radius: number,
  centre: number,
): string {
  return values
    .map((value, index) => {
      // -90° puts the first scale at the top; 60° per step around six scales.
      const angle = ((Math.PI * 2) / values.length) * index - Math.PI / 2;
      const scale = max > 0 ? Math.max(0, Math.min(value / max, 1)) : 0;
      const x = centre + Math.cos(angle) * radius * scale;
      const y = centre + Math.sin(angle) * radius * scale;
      return `${round(x)},${round(y)}`;
    })
    .join(" ");
}

/** Percentage of a domain's range, for the personality bars. */
export function barPercent(value: number, min: number, max: number): number {
  if (max <= min) {
    return 0;
  }
  const ratio = (value - min) / (max - min);
  return Math.round(Math.max(0, Math.min(ratio, 1)) * 100);
}

/**
 * "3 days" / "4 hours" / "just now" — how long a session has waited (§9.1).
 *
 * Coarse on purpose. The queue is sorted oldest-first and §9.4 escalates at
 * five days; nobody needs minutes, and a precise figure invites reading the
 * queue as a performance metric rather than a workload.
 */
export function waitingFor(submittedAt: string | null, now: Date): string {
  if (!submittedAt) {
    return "—";
  }

  const submitted = new Date(submittedAt);
  const ms = now.getTime() - submitted.getTime();
  if (Number.isNaN(ms)) {
    return "—";
  }
  // A clock skew between the database and the renderer can put a just-written
  // row slightly in the future. "in 1 hour" beside a waiting student is absurd;
  // clamp rather than display it.
  if (ms < 0) {
    return "just now";
  }

  const hours = Math.floor(ms / 3_600_000);
  if (hours < 1) return "just now";
  if (hours < 24) return `${hours} ${hours === 1 ? "hour" : "hours"}`;

  const days = Math.floor(hours / 24);
  return `${days} ${days === 1 ? "day" : "days"}`;
}

/** Sessions waiting longer than this are overdue (§9.4's five-day nudge). */
export const REVIEW_BACKLOG_DAYS = 5;

export function isOverdue(submittedAt: string | null, now: Date): boolean {
  if (!submittedAt) {
    return false;
  }
  const ms = now.getTime() - new Date(submittedAt).getTime();
  if (Number.isNaN(ms)) {
    return false;
  }
  return ms >= REVIEW_BACKLOG_DAYS * 24 * 3_600_000;
}

/**
 * Review status as a reviewer reads it.
 *
 * `needs_more_info` deliberately does not say "rejected" or "failed" — the
 * psychologist asked for another conversation, which is ordinary practice
 * rather than a problem with the student (R7 forbids clinical framing).
 */
export const REVIEW_STATUS_LABEL: Record<string, string> = {
  in_progress: "Draft saved",
  confirmed: "Confirmed",
  needs_more_info: "Waiting for a follow-up",
};

function round(value: number): number {
  return Math.round(value * 100) / 100;
}
