/**
 * The `intended_field` controlled vocabulary (plan.md §20 item 4).
 *
 * **This is a starter list. Revise it with a counsellor before the pilot.**
 *
 * plan.md v2 says the vocabulary is "unchanged from v1", and the v1 document is
 * not in this repository — so these values were chosen here rather than carried
 * over. Treat the list as a decision that has been made provisionally, not as
 * one that was inherited.
 *
 * Why a controlled vocabulary at all, rather than free text: M11 reports the
 * intended-field congruence rate (§10), which groups students by this column
 * and compares each group against its interest centroid. Free text makes that
 * number uncomputable — "pre-med", "MBBS", "Medicine" and "medical" arrive as
 * four fields with an n of 1 each, and §9's statistical honesty rule then
 * forbids printing a percentage on any of them.
 *
 * Changing a value here after a pilot orphans the rows already imported with
 * the old one. Add freely; rename only with a migration that rewrites
 * `participants.intended_field`.
 */

export const INTENDED_FIELDS = [
  "medicine",
  "engineering",
  "computing",
  "business_commerce",
  "law",
  "social_sciences",
  "natural_sciences",
  "education",
  "arts_design",
  "agriculture",
  "media",
  "undecided",
] as const;

export type IntendedField = (typeof INTENDED_FIELDS)[number];

/**
 * Display labels. Kept beside the values so a new field cannot be added without
 * someone deciding what a student sees.
 *
 * "Undecided" is a first-class answer, not a missing one — a seventeen-year-old
 * who has not chosen is exactly who this product is for, and forcing a guess
 * would corrupt the congruence figure it feeds.
 */
const LABELS: Record<IntendedField, string> = {
  medicine: "Medicine and health sciences",
  engineering: "Engineering",
  computing: "Computing and IT",
  business_commerce: "Business and commerce",
  law: "Law",
  social_sciences: "Social sciences",
  natural_sciences: "Natural sciences",
  education: "Education",
  arts_design: "Arts and design",
  agriculture: "Agriculture and veterinary",
  media: "Media and communication",
  undecided: "Undecided",
};

export function isIntendedField(value: string): value is IntendedField {
  return (INTENDED_FIELDS as readonly string[]).includes(value);
}

export function describeIntendedField(value: string | null): string {
  if (!value) return "—";
  return isIntendedField(value) ? LABELS[value] : value;
}

/** For error messages and the CSV template. */
export const INTENDED_FIELDS_SENTENCE = INTENDED_FIELDS.join(", ");
