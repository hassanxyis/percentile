import "server-only";

import { requireReviewer } from "@/lib/dal";
import { createClient } from "@/lib/supabase/server";
import type { QualityFlag } from "@/lib/review";

/**
 * One session's full engine output, for the review screen (plan.md §9.1).
 *
 * RLS-bound, for the reason given in `../queries.ts`: this is the screen where
 * `interview_notes` lives, and reading it through the same policies the
 * database tests assert is what keeps the counsellor boundary honest.
 *
 * Everything is loaded against the LATEST engine version present for the
 * session rather than against `SCORING_ENGINE_VERSION`. `scores` and
 * `occupation_matches` are keyed by `(session_id, engine_version)` so a rescore
 * writes a new row beside the old one (R1) — pinning to the engine's current
 * version would blank the screen for every session scored before the last bump,
 * which is the opposite of what that history is for.
 */

export type InterestScores = {
  raw: Record<string, number>;
  code: string;
  differentiation: number;
  band: string;
  code_provisional?: boolean;
  tie_broken?: boolean;
  norms_status?: string;
};

export type PersonalityScores = {
  raw: Record<string, number>;
  bands: Record<string, string>;
  labels?: Record<string, string>;
  norms_status?: string;
};

/** GET2, or the `module_not_administered` shape (R10). Never assume subscales. */
export type Get2Scores =
  | {
      instrument: "GET2";
      subscales: Record<string, number>;
      get2_total: number;
      entrepreneurial_band: string;
      entrepreneurial_flag?: boolean;
    }
  | { instrument: null; status: string };

export type OccupationMatch = {
  rank: number;
  onet_soc_code: string;
  score: number;
  local_title: string | null;
  local_pathway: string | null;
  title: string | null;
  pk_relevant: boolean;
};

export type ExistingDirection = {
  rank: number;
  onet_soc_code: string | null;
  local_title: string;
  rationale: string | null;
};

export type ReviewDetail = {
  session_id: string;
  participant: {
    id: string;
    full_name: string;
    intended_field: string | null;
    education_level: string | null;
    status: string;
  };
  cohort_name: string;
  submitted_at: string | null;
  engine_version: string | null;
  interests: InterestScores | null;
  personality: PersonalityScores | null;
  get2: Get2Scores | null;
  flags: QualityFlag[];
  matches: OccupationMatch[];
  review: {
    id: string;
    status: string;
    interview_mode: string | null;
    interview_notes: string | null;
  } | null;
  directions: ExistingDirection[];
};

export async function loadReview(sessionId: string): Promise<ReviewDetail | null> {
  await requireReviewer();
  const supabase = await createClient();

  const { data: session } = await supabase
    .from("sessions")
    .select(
      "id, submitted_at, " +
        "participants!inner(id, full_name, intended_field, education_level, status, " +
        "cohorts(name))",
    )
    .eq("id", sessionId)
    .maybeSingle<{
      id: string;
      submitted_at: string | null;
      participants: {
        id: string;
        full_name: string;
        intended_field: string | null;
        education_level: string | null;
        status: string;
        cohorts: { name: string } | null;
      } | null;
    }>();

  // Null means RLS did not return it: no such session, or another school's.
  // Both become a 404 — distinguishing them would confirm the session exists.
  if (!session?.participants) {
    return null;
  }

  // Latest score for this session, whatever version produced it.
  const { data: score } = await supabase
    .from("scores")
    .select("engine_version, interests, personality, values_scores, flags, scored_at")
    .eq("session_id", sessionId)
    .order("scored_at", { ascending: false })
    .limit(1)
    .maybeSingle<{
      engine_version: string;
      interests: InterestScores;
      personality: PersonalityScores;
      values_scores: Get2Scores;
      flags: QualityFlag[] | null;
    }>();

  // Matched to the score's engine version, so the occupation list and the
  // profile above it always describe the same scoring run.
  let matches: OccupationMatch[] = [];
  if (score) {
    const { data } = await supabase
      .from("occupation_matches")
      .select("rank, onet_soc_code, score, local_title, local_pathway, occupations(title)")
      .eq("session_id", sessionId)
      .eq("engine_version", score.engine_version)
      .order("rank", { ascending: true })
      .returns<
        {
          rank: number;
          onet_soc_code: string;
          score: number;
          local_title: string | null;
          local_pathway: string | null;
          occupations: { title: string } | null;
        }[]
      >();

    matches = (data ?? []).map((row) => ({
      rank: row.rank,
      onet_soc_code: row.onet_soc_code,
      score: row.score,
      local_title: row.local_title,
      local_pathway: row.local_pathway,
      title: row.occupations?.title ?? null,
      // `occupation_matches` does not carry pk_relevant; a localised title is
      // what the matcher writes for a Pakistan-mapped occupation (§8), so it is
      // the honest signal for the "local" grouping on screen.
      pk_relevant: row.local_title !== null,
    }));
  }

  const { data: review } = await supabase
    .from("reviews")
    .select("id, status, interview_mode, interview_notes")
    .eq("session_id", sessionId)
    .maybeSingle<{
      id: string;
      status: string;
      interview_mode: string | null;
      interview_notes: string | null;
    }>();

  let directions: ExistingDirection[] = [];
  if (review) {
    const { data } = await supabase
      .from("career_directions")
      .select("rank, onet_soc_code, local_title, rationale")
      .eq("review_id", review.id)
      .order("rank", { ascending: true })
      .returns<ExistingDirection[]>();
    directions = data ?? [];
  }

  return {
    session_id: session.id,
    participant: {
      id: session.participants.id,
      full_name: session.participants.full_name,
      intended_field: session.participants.intended_field,
      education_level: session.participants.education_level,
      status: session.participants.status,
    },
    cohort_name: session.participants.cohorts?.name ?? "—",
    submitted_at: session.submitted_at,
    engine_version: score?.engine_version ?? null,
    interests: score?.interests ?? null,
    personality: score?.personality ?? null,
    // `values_scores` holds the GET2 shape in v2 — the column was not renamed,
    // so rescoreable history stays intact (0003_reviews.sql's comment).
    get2: score?.values_scores ?? null,
    flags: score?.flags ?? [],
    matches,
    review,
    directions,
  };
}

/** Occupations a psychologist can attach to a direction, for the picker. */
export type OccupationOption = {
  onet_soc_code: string;
  title: string;
  pk_title: string | null;
};

/**
 * The catalogue for the direction picker.
 *
 * Pakistan-mapped occupations first: §8 leads with them everywhere else, and a
 * psychologist choosing a direction for a Pakistani student should meet the
 * localised list before the international one. `occupations_read` grants every
 * authenticated user select on this table (0002_rls.sql) — it is reference
 * data, not tenant data.
 */
export async function listOccupations(): Promise<OccupationOption[]> {
  await requireReviewer();
  const supabase = await createClient();

  const { data } = await supabase
    .from("occupations")
    .select("onet_soc_code, title, pk_title, pk_relevant")
    .order("pk_relevant", { ascending: false })
    .order("title", { ascending: true })
    .returns<(OccupationOption & { pk_relevant: boolean })[]>();

  return (data ?? []).map(({ onet_soc_code, title, pk_title }) => ({
    onet_soc_code,
    title,
    pk_title,
  }));
}
