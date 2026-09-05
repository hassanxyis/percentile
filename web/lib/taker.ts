/**
 * Taker-flow logic (plan.md §13, M6).
 *
 * Pure: items and answers in, decisions out. No I/O, no Supabase, no Next
 * imports — the same shape as `roster-csv.ts`, and for the same reason. M6's
 * "done when" is *resume with nothing lost*, and resume is a function of the
 * answers already recorded. Keeping that function pure is what lets
 * `taker.test.ts` prove it without a phone and a database.
 *
 * The organising idea: **an item's own response range decides how it renders.**
 * Nothing here switches on `instrument_code` to pick a widget. Interests are
 * 0..1 and get two buttons; personality is 1..5 and gets a five-point scale;
 * GET2, whenever §20 item 2 is settled and the file lands, is 0..2 and will get
 * a three-point scale from this same code. R10 says adding GET2 must be loading
 * a file rather than editing the taker flow, and this is where that promise is
 * either kept or broken.
 */

/**
 * Presentation order. Filtered against what is actually in the database, so an
 * instrument with no items simply does not appear.
 *
 * `get2` is listed and is expected to be absent: `data/instruments/get2_items.csv`
 * does not exist, `load_instruments.py` skips it with a warning, and two modules
 * is the shape that ships today (plan.md §20 item 2). Listing it here costs
 * nothing and means the file landing is the only change required.
 */
export const MODULE_ORDER = ["interests", "personality", "get2"] as const;

/** Upper bound on a single item's recorded time, in milliseconds (2 minutes).
 *
 *  Mirrors the clamp in `record_response` (0006_taker_flow.sql). `flags.py`
 *  sums ms_elapsed and compares the total against six minutes to decide
 *  `too_fast`; one item left open during a lunch break would otherwise add an
 *  hour to that sum and hide a session that was genuinely rushed. Clamping in
 *  both places is deliberate — the database is the enforcement point, this one
 *  keeps the number honest before it leaves the browser. */
export const MAX_MS_ELAPSED = 120_000;

export type TakerItem = {
  id: string;
  instrument_code: string;
  ordinal: number;
  code: string;
  text: string;
  response_min: number;
  response_max: number;
};

export type TakerModule = {
  code: string;
  items: TakerItem[];
};

/** How an item is answered. Derived from its range, never from its instrument. */
export type Widget = "binary" | "scale";

export function widgetForItem(item: TakerItem): Widget {
  return item.response_max - item.response_min === 1 ? "binary" : "scale";
}

/**
 * Group items into modules in presentation order.
 *
 * Items are sorted by `ordinal` within a module — the column exists for exactly
 * this and the database makes no ordering promise without an ORDER BY. An
 * instrument not named in `MODULE_ORDER` is dropped rather than appended: the
 * order students see should be a decision in this file, not a consequence of
 * what happened to be loaded.
 */
export function groupIntoModules(items: TakerItem[]): TakerModule[] {
  return MODULE_ORDER.map((code) => ({
    code,
    items: items
      .filter((item) => item.instrument_code === code)
      .sort((a, b) => a.ordinal - b.ordinal),
  })).filter((module) => module.items.length > 0);
}

/** Every item across every module, in the order they are presented. */
export function flattenModules(modules: TakerModule[]): TakerItem[] {
  return modules.flatMap((module) => module.items);
}

/**
 * Where to resume: the first item with no recorded answer.
 *
 * Derived from `responses` rather than from `sessions.progress`, which is R1
 * applied to the UI — the raw answers are the record, and anything else is a
 * convenience that can drift. `progress` is a counsellor-facing summary; if the
 * two ever disagree, the answers are right.
 *
 * Returns `items.length` when everything is answered, which reads naturally as
 * "past the end" and is what the caller tests to show the finish screen.
 */
export function nextUnansweredIndex(
  items: TakerItem[],
  answeredItemIds: Iterable<string>,
): number {
  const answered = answeredItemIds instanceof Set ? answeredItemIds : new Set(answeredItemIds);
  const index = items.findIndex((item) => !answered.has(item.id));
  return index === -1 ? items.length : index;
}

export type ModuleProgress = {
  code: string;
  /** Segments in the path — one per item in this module. */
  total: number;
  /** How many are answered. */
  answered: number;
  /** Position within this module, or null when the student is elsewhere. */
  currentIndex: number | null;
  /** True once every item in the module has an answer. */
  complete: boolean;
};

/**
 * The progress path (§13).
 *
 * "Progress reads as a path, not a bar" — per-module segments with the current
 * position marked, rather than a raw 47/110 counter. This returns the numbers;
 * the component draws them.
 *
 * Per module rather than one global bar because 110 items expressed as a single
 * line is a discouraging thing to look at, and because "you have finished the
 * interests section" is the honest, legible unit of progress.
 */
export function progressForModules(
  modules: TakerModule[],
  answeredItemIds: Iterable<string>,
  currentIndex: number,
): ModuleProgress[] {
  const answered = answeredItemIds instanceof Set ? answeredItemIds : new Set(answeredItemIds);
  let offset = 0;

  return modules.map((module) => {
    const start = offset;
    offset += module.items.length;
    const withinModule = currentIndex >= start && currentIndex < offset;

    return {
      code: module.code,
      total: module.items.length,
      answered: module.items.filter((item) => answered.has(item.id)).length,
      currentIndex: withinModule ? currentIndex - start : null,
      complete: module.items.every((item) => answered.has(item.id)),
    };
  });
}

/**
 * Which module an absolute item index falls in, and where the module boundaries
 * are. The taker shows a transition screen when crossing one (§13).
 */
export function moduleIndexForItem(modules: TakerModule[], index: number): number {
  let offset = 0;
  for (let i = 0; i < modules.length; i += 1) {
    offset += modules[i].items.length;
    if (index < offset) {
      return i;
    }
  }
  return modules.length - 1;
}

/** Clamp a measured duration to something a scoring flag can trust. */
export function clampMsElapsed(ms: number): number {
  if (!Number.isFinite(ms) || ms < 0) {
    return 0;
  }
  return Math.min(Math.round(ms), MAX_MS_ELAPSED);
}

/**
 * Whether a value is a legal answer to this item.
 *
 * The database checks this too (`record_response` validates against the item's
 * own range and raises). Checking here as well turns a network round trip and a
 * Postgres exception into a no-op, and neither check is redundant: this one is
 * reachable only through our own UI, and that one is the boundary.
 */
export function isValidResponse(item: TakerItem, value: number): boolean {
  return (
    Number.isInteger(value) &&
    value >= item.response_min &&
    value <= item.response_max
  );
}
