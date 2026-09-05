"use client";

import { useActionState, useState } from "react";

import {
  confirmReview,
  saveDraft,
  sendBack,
  type ReviewActionState,
} from "./actions";
import type { ExistingDirection, OccupationOption } from "./queries";

/**
 * The psychologist's own input (plan.md §9.1).
 *
 * Three buttons, one form, one server action each. They share the form's fields
 * because they are three outcomes of the same piece of work, not three
 * different forms — a psychologist who typed notes and then decided to send
 * back should not lose the notes.
 *
 * **R3 lives here.** `interview_notes` is the psychologist's clinical voice:
 * never model-generated, never auto-filled from the scores, never pre-populated
 * with a suggestion. The textarea starts empty on a new review and holds only
 * what a person typed. The same goes for each direction's rationale — the
 * occupation picker offers catalogue titles, and the reasoning is theirs.
 */

const FIELD = "rounded border border-black/20 px-3 py-2 dark:border-white/20";
const MAX_DIRECTIONS = 3;

const INTERVIEW_MODES = [
  { value: "", label: "Not recorded" },
  { value: "in_person", label: "In person" },
  { value: "video", label: "Video call" },
  { value: "async_notes", label: "Notes only, no conversation" },
];

type DirectionDraft = {
  code: string;
  title: string;
  rationale: string;
};

function toDraft(direction: ExistingDirection | undefined): DirectionDraft {
  return {
    code: direction?.onet_soc_code ?? "",
    title: direction?.local_title ?? "",
    rationale: direction?.rationale ?? "",
  };
}

export function ReviewForm({
  sessionId,
  occupations,
  flagCodes,
  existing,
  directions,
  confirmed,
}: {
  sessionId: string;
  occupations: OccupationOption[];
  flagCodes: string[];
  existing: {
    status: string;
    interview_mode: string | null;
    interview_notes: string | null;
  } | null;
  directions: ExistingDirection[];
  confirmed: boolean;
}) {
  /**
   * One action, dispatching on the submit button's own value.
   *
   * A `formAction` per button cannot work from a client component — it would
   * need `"use server"` inside a `"use client"` file. The alternative, three
   * separate forms, would put the notes in only one of them and lose them on
   * the other two paths.
   *
   * So the intent rides in the form data. `SubmitButton` below sets
   * `name="intent"` on each button, and the browser submits the value of the
   * button that was actually pressed — the standard multi-submit form. An
   * absent or unrecognised intent falls through to `saveDraft`, which is the
   * safe outcome: never an accidental sign-off.
   */
  const [state, formAction, pending] = useActionState<ReviewActionState, FormData>(
    async (_previous, formData) => {
      const intent = String(formData.get("intent") ?? "");
      if (intent === "confirm") return confirmReview(_previous, formData);
      if (intent === "send_back") return sendBack(_previous, formData);
      return saveDraft(_previous, formData);
    },
    { status: "idle" },
  );

  const [rows, setRows] = useState<DirectionDraft[]>(() =>
    Array.from({ length: MAX_DIRECTIONS }, (_, index) => toDraft(directions[index])),
  );

  function update(index: number, patch: Partial<DirectionDraft>) {
    setRows((previous) =>
      previous.map((row, i) => (i === index ? { ...row, ...patch } : row)),
    );
  }

  const filled = rows.filter((row) => row.title.trim() || row.code).length;

  return (
    <form action={formAction} className="flex flex-col gap-6">
      <input type="hidden" name="session_id" value={sessionId} />
      {/* Which flags this reviewer had in front of them when they decided. A
          later flags.py change cannot rewrite what they actually saw. */}
      <input type="hidden" name="flags_reviewed" value={JSON.stringify(flagCodes)} />

      <section className="flex flex-col gap-3">
        <h2 className="text-sm font-medium uppercase tracking-wide">
          Interview notes
        </h2>
        <p className="text-xs text-black/60 dark:text-white/60">
          Your own words, and only yours. These are not shown to the student and
          are not visible to counsellors.
        </p>

        <label className="flex flex-col gap-1 text-sm">
          <span>How did the conversation happen?</span>
          <select
            name="interview_mode"
            defaultValue={existing?.interview_mode ?? ""}
            className={FIELD}
          >
            {INTERVIEW_MODES.map((mode) => (
              <option key={mode.value} value={mode.value}>
                {mode.label}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span>Notes</span>
          <textarea
            name="interview_notes"
            rows={8}
            defaultValue={existing?.interview_notes ?? ""}
            className={FIELD}
          />
        </label>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-sm font-medium uppercase tracking-wide">
          Career directions
        </h2>
        <p className="text-xs text-black/60 dark:text-white/60">
          Between one and three, in order. Pick an occupation from the catalogue
          or write the direction in your own words — both reach the student&rsquo;s
          report.
        </p>

        {rows.map((row, index) => (
          <fieldset
            key={index}
            className="flex flex-col gap-2 rounded border border-black/10 px-4 py-3 dark:border-white/15"
          >
            <legend className="px-1 text-xs text-black/50 dark:text-white/50">
              Direction {index + 1}
              {index > 0 && " (optional)"}
            </legend>

            <label className="flex flex-col gap-1 text-sm">
              <span>Occupation</span>
              <select
                name={`direction_${index}_code`}
                value={row.code}
                onChange={(event) => update(index, { code: event.target.value })}
                className={FIELD}
              >
                <option value="">No catalogue occupation</option>
                {occupations.map((occupation) => (
                  <option key={occupation.onet_soc_code} value={occupation.onet_soc_code}>
                    {occupation.pk_title ?? occupation.title}
                  </option>
                ))}
              </select>
            </label>

            {/* The title the student reads, defaulting to the catalogue's. Sent
                as a companion field so the action can fall back to it without
                looking the occupation up again. */}
            <input
              type="hidden"
              name={`direction_${index}_code_title`}
              value={titleForCode(occupations, row.code)}
            />

            <label className="flex flex-col gap-1 text-sm">
              <span>
                What the student sees
                <span className="text-black/50 dark:text-white/50">
                  {" "}
                  — leave blank to use the occupation name
                </span>
              </span>
              <input
                name={`direction_${index}_title`}
                value={row.title}
                onChange={(event) => update(index, { title: event.target.value })}
                placeholder={titleForCode(occupations, row.code) || "e.g. Family business"}
                className={FIELD}
              />
            </label>

            <label className="flex flex-col gap-1 text-sm">
              <span>Why this direction</span>
              <textarea
                name={`direction_${index}_rationale`}
                rows={2}
                value={row.rationale}
                onChange={(event) => update(index, { rationale: event.target.value })}
                className={FIELD}
              />
            </label>
          </fieldset>
        ))}
      </section>

      {state.status === "error" && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {state.message}
        </p>
      )}
      {state.status === "saved" && (
        <p role="status" className="text-sm text-green-700 dark:text-green-400">
          {state.message}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-3 border-t border-black/10 pt-4 dark:border-white/10">
        <button
          type="submit"
          name="intent"
          value="save_draft"
          disabled={pending}
          className="rounded border border-black/20 px-4 py-2 text-sm font-medium disabled:opacity-50 dark:border-white/20"
        >
          Save draft
        </button>

        <button
          type="submit"
          name="intent"
          value="confirm"
          disabled={pending || filled === 0}
          className="rounded bg-black px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-black"
        >
          {confirmed ? "Save changes" : "Confirm"}
        </button>

        <button
          type="submit"
          name="intent"
          value="send_back"
          disabled={pending}
          className="rounded border border-black/20 px-4 py-2 text-sm disabled:opacity-50 dark:border-white/20"
        >
          Send back for a follow-up
        </button>

        {filled === 0 && (
          <span className="text-xs text-black/50 dark:text-white/50">
            Confirming needs at least one direction.
          </span>
        )}
      </div>

      {/* §9.1: confirming enqueues the student's report. Said once, next to the
          button, rather than as a modal nobody reads. */}
      <p className="text-xs text-black/50 dark:text-white/50">
        Confirming releases the student&rsquo;s report for generation. Until then
        no report can be produced for them.
      </p>
    </form>
  );
}

function titleForCode(occupations: OccupationOption[], code: string): string {
  if (!code) return "";
  const match = occupations.find((occupation) => occupation.onet_soc_code === code);
  return match ? (match.pk_title ?? match.title) : "";
}
