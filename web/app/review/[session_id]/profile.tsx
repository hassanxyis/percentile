import { barPercent, flagMeaning, hexagonPoints, isProminentFlag } from "@/lib/review";
import type { QualityFlag } from "@/lib/review";

import type {
  Get2Scores,
  InterestScores,
  OccupationMatch,
  PersonalityScores,
} from "./queries";

/**
 * The engine's output, as a psychologist reads it (plan.md §9.1).
 *
 * Server components: this is all presentation of data the page already loaded,
 * and none of it is interactive. Charts are hand-written SVG, matching
 * `engine/app/report/charts.py` — no plotting library, because the output is a
 * hexagon and some bars, and the review screen should show the shape the
 * student's PDF will.
 *
 * **Nothing here interprets a result.** R3 reserves that for a person; §9.1 is
 * explicit that the psychologist's notes are never auto-filled from the scores.
 * These components label axes and print numbers.
 *
 * **No percentiles.** R4 forbids them until a `norms` row for the population
 * reaches n >= 300, and every scorer returns `norms_status:
 * "pending_local_norms"` today. Raw scores and provisional bands only — and the
 * band names come from the engine, not from a threshold re-implemented here.
 */

const RIASEC = ["R", "I", "A", "S", "E", "C"] as const;

const RIASEC_LABELS: Record<string, string> = {
  R: "Realistic",
  I: "Investigative",
  A: "Artistic",
  S: "Social",
  E: "Enterprising",
  C: "Conventional",
};

// O*NET Interest Profiler Short Form: 10 checkbox items per scale, so each
// scores 0..10 (engine/app/scoring/interests.py).
const INTEREST_MAX = 10;

// IPIP 50-item: 10 items per domain at 1..5, so each domain scores 10..50.
const PERSONALITY_MIN = 10;
const PERSONALITY_MAX = 50;

const BAND_LABELS: Record<string, string> = {
  well_differentiated: "clearly differentiated",
  moderate: "moderately differentiated",
  undifferentiated: "not clearly differentiated",
};

export function InterestSection({ interests }: { interests: InterestScores | null }) {
  if (!interests) {
    return <Missing what="Interest scores" />;
  }

  const values = RIASEC.map((scale) => interests.raw?.[scale] ?? 0);
  const size = 220;
  const centre = size / 2;
  const radius = centre - 28;

  return (
    <section className="flex flex-col gap-4">
      <SectionHeading
        title="Interests"
        note={`Holland code ${interests.code} · ${
          BAND_LABELS[interests.band] ?? interests.band
        }`}
      />

      <div className="flex flex-wrap items-center gap-8">
        <svg
          width={size}
          height={size}
          viewBox={`0 0 ${size} ${size}`}
          role="img"
          aria-label={`Interest profile, Holland code ${interests.code}`}
          className="shrink-0"
        >
          {/* Reference rings at a quarter, half, three-quarters and full, so a
              shape can be read as a level rather than only compared to itself. */}
          {[0.25, 0.5, 0.75, 1].map((ring) => (
            <polygon
              key={ring}
              points={hexagonPoints(
                RIASEC.map(() => INTEREST_MAX * ring),
                INTEREST_MAX,
                radius,
                centre,
              )}
              className="fill-none stroke-black/10 dark:stroke-white/15"
              strokeWidth={1}
            />
          ))}

          <polygon
            points={hexagonPoints(values, INTEREST_MAX, radius, centre)}
            className="fill-black/20 stroke-black/70 dark:fill-white/20 dark:stroke-white/70"
            strokeWidth={2}
          />

          {RIASEC.map((scale, index) => {
            const angle = ((Math.PI * 2) / RIASEC.length) * index - Math.PI / 2;
            const x = centre + Math.cos(angle) * (radius + 14);
            const y = centre + Math.sin(angle) * (radius + 14);
            return (
              <text
                key={scale}
                x={x}
                y={y}
                textAnchor="middle"
                dominantBaseline="middle"
                className="fill-black/60 text-[11px] dark:fill-white/60"
              >
                {scale}
              </text>
            );
          })}
        </svg>

        <dl className="grid grid-cols-2 gap-x-8 gap-y-1 text-sm">
          {RIASEC.map((scale) => (
            <div key={scale} className="contents">
              <dt className="text-black/60 dark:text-white/60">
                {RIASEC_LABELS[scale]}
              </dt>
              <dd className="tabular-nums">
                {interests.raw?.[scale] ?? 0} / {INTEREST_MAX}
              </dd>
            </div>
          ))}
        </dl>
      </div>

      {/* §7.1: the third letter is unstable when the scale that took it barely
          beat the one that missed. The screen says so rather than presenting a
          three-letter code as settled. */}
      {interests.code_provisional && (
        <p className="text-sm text-black/60 dark:text-white/60">
          The third letter of this code is provisional — the third and fourth
          scales scored close together.
        </p>
      )}
    </section>
  );
}

export function PersonalitySection({
  personality,
}: {
  personality: PersonalityScores | null;
}) {
  if (!personality) {
    return <Missing what="Personality scores" />;
  }

  // Order and labels come from the engine when present. The fifth domain is
  // Emotional Stability, not Neuroticism — a report handed to a seventeen-year-
  // old should not lead with a negatively framed trait name.
  const domains = ["O", "C", "E", "A", "S"];

  return (
    <section className="flex flex-col gap-4">
      <SectionHeading title="Personality" note="Provisional bands — no local norms yet" />

      <div className="flex flex-col gap-3">
        {domains.map((domain) => {
          const raw = personality.raw?.[domain] ?? PERSONALITY_MIN;
          const percent = barPercent(raw, PERSONALITY_MIN, PERSONALITY_MAX);
          return (
            <div key={domain} className="flex flex-col gap-1">
              <div className="flex items-baseline justify-between text-sm">
                <span>{personality.labels?.[domain] ?? domain}</span>
                <span className="text-black/60 tabular-nums dark:text-white/60">
                  {raw} / {PERSONALITY_MAX} · {personality.bands?.[domain] ?? "—"}
                </span>
              </div>
              <div
                className="h-2 w-full rounded-full bg-black/10 dark:bg-white/15"
                role="img"
                aria-label={`${personality.labels?.[domain] ?? domain}: ${
                  personality.bands?.[domain] ?? "unknown"
                }`}
              >
                <div
                  className="h-2 rounded-full bg-black/70 dark:bg-white/70"
                  style={{ width: `${percent}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

/**
 * GET2, or a plain statement that it was not administered.
 *
 * R10: the module is provisional pending written permission, and
 * `score_get2(responses, None)` returns `{instrument: null, status:
 * "module_not_administered"}` — which is every session today. This branch is
 * the one R10 requires every downstream template to have from day one, rather
 * than adding later under deadline pressure.
 */
export function Get2Section({ get2 }: { get2: Get2Scores | null }) {
  if (!get2 || get2.instrument === null) {
    return (
      <section className="flex flex-col gap-2">
        <SectionHeading title="Entrepreneurial tendency" />
        <p className="text-sm text-black/60 dark:text-white/60">
          Not administered.
        </p>
      </section>
    );
  }

  return (
    <section className="flex flex-col gap-4">
      <SectionHeading
        title="Entrepreneurial tendency"
        note={`GET2 · ${get2.entrepreneurial_band} · provisional, pending permission`}
      />

      <div className="flex flex-col gap-3">
        {Object.entries(get2.subscales).map(([scale, percent]) => (
          <div key={scale} className="flex flex-col gap-1">
            <div className="flex items-baseline justify-between text-sm">
              <span className="capitalize">{scale.replace("_", " ")}</span>
              <span className="text-black/60 tabular-nums dark:text-white/60">
                {percent}%
              </span>
            </div>
            <div className="h-2 w-full rounded-full bg-black/10 dark:bg-white/15">
              <div
                className="h-2 rounded-full bg-black/70 dark:bg-white/70"
                style={{ width: `${Math.max(0, Math.min(percent, 100))}%` }}
              />
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

/**
 * Quality flags — prominent, not buried (§9.1).
 *
 * `warn`-level flags and `undifferentiated` are visually distinct from `info`,
 * because they change what the review conversation needs to cover. R8 keeps
 * these off the student's own report entirely; this screen and the counsellor's
 * cohort copy are the only places they appear.
 */
export function FlagsSection({ flags }: { flags: QualityFlag[] }) {
  if (flags.length === 0) {
    return (
      <section className="flex flex-col gap-2">
        <SectionHeading title="Response quality" />
        <p className="text-sm text-black/60 dark:text-white/60">
          Nothing flagged.
        </p>
      </section>
    );
  }

  return (
    <section className="flex flex-col gap-3">
      <SectionHeading title="Response quality" />

      <ul className="flex flex-col gap-3">
        {flags.map((flag) => {
          const prominent = isProminentFlag(flag);
          return (
            <li
              key={flag.code}
              className={`rounded border px-4 py-3 text-sm ${
                prominent
                  ? "border-amber-500/50 bg-amber-500/10"
                  : "border-black/10 dark:border-white/15"
              }`}
            >
              <p className="font-medium">
                {flag.code.replace(/_/g, " ")}
                <span className="font-normal text-black/60 dark:text-white/60">
                  {" "}
                  — {flag.detail}
                </span>
              </p>
              <p className="mt-1 text-black/70 dark:text-white/70">
                {flagMeaning(flag.code)}
              </p>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/**
 * The top matches (§9.1 asks for the top 15).
 *
 * Split local-first, matching §8's ordering everywhere else. The score is shown
 * as a plain 0–1 similarity: it is a shape comparison between interest
 * profiles, not a probability of success, and rendering it as a percentage
 * invites reading it as one.
 */
export function MatchesSection({ matches }: { matches: OccupationMatch[] }) {
  if (matches.length === 0) {
    return (
      <section className="flex flex-col gap-2">
        <SectionHeading title="Occupation matches" />
        <p className="text-sm text-black/60 dark:text-white/60">
          No matches were computed for this session. The scoring run may have
          been interrupted — it does not mean nothing suited this student.
        </p>
      </section>
    );
  }

  return (
    <section className="flex flex-col gap-3">
      <SectionHeading title="Occupation matches" note={`${matches.length} shown`} />

      <table className="text-sm">
        <thead>
          <tr className="text-left text-xs uppercase text-black/50 dark:text-white/50">
            <th className="py-2 pr-4 font-medium">#</th>
            <th className="py-2 pr-4 font-medium">Occupation</th>
            <th className="py-2 pr-4 font-medium">Pathway</th>
            <th className="py-2 font-medium">Fit</th>
          </tr>
        </thead>
        <tbody>
          {matches.map((match) => (
            <tr
              key={`${match.rank}-${match.onet_soc_code}`}
              className="border-t border-black/10 dark:border-white/10"
            >
              <td className="py-2 pr-4 tabular-nums text-black/50 dark:text-white/50">
                {match.rank}
              </td>
              <td className="py-2 pr-4">
                {match.local_title ?? match.title ?? match.onet_soc_code}
                {match.local_title && match.title && (
                  <span className="text-black/50 dark:text-white/50">
                    {" "}
                    · {match.title}
                  </span>
                )}
              </td>
              <td className="py-2 pr-4 text-black/60 dark:text-white/60">
                {match.local_pathway ?? "—"}
              </td>
              <td className="py-2 tabular-nums">{Number(match.score).toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function SectionHeading({ title, note }: { title: string; note?: string }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-black/10 pb-2 dark:border-white/10">
      <h2 className="text-sm font-medium uppercase tracking-wide">{title}</h2>
      {note && (
        <span className="text-xs text-black/50 dark:text-white/50">{note}</span>
      )}
    </div>
  );
}

/**
 * A section with no data.
 *
 * Says the score is missing rather than rendering an empty chart. A blank
 * hexagon reads as "this student has no interests", which is both false and the
 * kind of thing a reviewer might repeat to a student.
 */
function Missing({ what }: { what: string }) {
  return (
    <section className="flex flex-col gap-2">
      <SectionHeading title={what} />
      <p className="text-sm text-black/60 dark:text-white/60">
        Not available for this session. It may not have finished scoring yet.
      </p>
    </section>
  );
}
