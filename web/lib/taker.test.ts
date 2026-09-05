import { describe, expect, it } from "vitest";

import {
  clampMsElapsed,
  flattenModules,
  groupIntoModules,
  isValidResponse,
  MAX_MS_ELAPSED,
  moduleIndexForItem,
  nextUnansweredIndex,
  progressForModules,
  widgetForItem,
  type TakerItem,
} from "@/lib/taker";

/**
 * M6's "done when" (plan.md §17) is a phone test: complete the assessment, kill
 * the connection mid-module, reopen, resume with nothing lost. The half of that
 * which can be proved without a phone is *resume*, and it lives in
 * `nextUnansweredIndex`. It gets the most attention here.
 *
 * The other thing worth pinning down is that no widget decision reads
 * `instrument_code`. R10 requires that adding GET2 later is loading a file, not
 * editing this flow, and a test is the only thing that keeps that true.
 */

function item(overrides: Partial<TakerItem> & { id: string }): TakerItem {
  return {
    instrument_code: "interests",
    ordinal: 1,
    code: "IP_R_01",
    text: "Build kitchen cabinets",
    response_min: 0,
    response_max: 1,
    ...overrides,
  };
}

/** A stand-in for the real instruments: 0..1 interests, 1..5 personality. */
function items(interests: number, personality: number): TakerItem[] {
  return [
    ...Array.from({ length: interests }, (_, i) =>
      item({
        id: `i${i}`,
        instrument_code: "interests",
        ordinal: i + 1,
        code: `IP_R_${i + 1}`,
        response_min: 0,
        response_max: 1,
      }),
    ),
    ...Array.from({ length: personality }, (_, i) =>
      item({
        id: `p${i}`,
        instrument_code: "personality",
        ordinal: i + 1,
        code: `IPIP_E_${i + 1}`,
        response_min: 1,
        response_max: 5,
      }),
    ),
  ];
}

describe("grouping into modules", () => {
  it("orders modules by MODULE_ORDER, not by the order rows arrive", () => {
    const shuffled = [...items(2, 2)].reverse();

    const modules = groupIntoModules(shuffled);

    expect(modules.map((m) => m.code)).toEqual(["interests", "personality"]);
  });

  it("sorts items within a module by ordinal", () => {
    const outOfOrder = [
      item({ id: "b", ordinal: 2 }),
      item({ id: "a", ordinal: 1 }),
      item({ id: "c", ordinal: 3 }),
    ];

    const [module] = groupIntoModules(outOfOrder);

    expect(module.items.map((i) => i.id)).toEqual(["a", "b", "c"]);
  });

  it("drops an instrument that has no items", () => {
    // Today's shape: get2_items.csv does not exist, so `instruments` has no
    // get2 row and two modules is what ships (plan.md §20 item 2).
    const modules = groupIntoModules(items(60, 50));

    expect(modules).toHaveLength(2);
    expect(modules.map((m) => m.code)).not.toContain("get2");
  });

  it("includes GET2 when its items appear, with no other change", () => {
    // R10: adding GET2 must be loading a file, not editing the taker flow.
    const withGet2 = [
      ...items(2, 2),
      item({
        id: "g0",
        instrument_code: "get2",
        ordinal: 1,
        code: "GET2_ACH_01",
        response_min: 0,
        response_max: 2,
      }),
    ];

    const modules = groupIntoModules(withGet2);

    expect(modules.map((m) => m.code)).toEqual(["interests", "personality", "get2"]);
  });

  it("ignores an instrument nobody put in MODULE_ORDER", () => {
    const stray = [...items(1, 1), item({ id: "v0", instrument_code: "values" })];

    const modules = groupIntoModules(stray);

    expect(modules.map((m) => m.code)).toEqual(["interests", "personality"]);
  });

  it("survives an empty items table (R2)", () => {
    expect(groupIntoModules([])).toEqual([]);
  });
});

describe("choosing a widget", () => {
  it("gives interests two buttons", () => {
    expect(widgetForItem(item({ id: "i", response_min: 0, response_max: 1 }))).toBe("binary");
  });

  it("gives personality a scale", () => {
    expect(widgetForItem(item({ id: "p", response_min: 1, response_max: 5 }))).toBe("scale");
  });

  it("gives GET2's 0..2 a scale without knowing what GET2 is", () => {
    // The decision is the range, never the instrument code. If this ever starts
    // switching on instrument_code, R10's promise is broken and this fails.
    const get2 = item({
      id: "g",
      instrument_code: "get2",
      response_min: 0,
      response_max: 2,
    });

    expect(widgetForItem(get2)).toBe("scale");
  });
});

describe("resuming", () => {
  const all = items(3, 2);
  const order = flattenModules(groupIntoModules(all));

  it("starts at the beginning when nothing is answered", () => {
    expect(nextUnansweredIndex(order, [])).toBe(0);
  });

  it("returns to the first gap, not the furthest point reached", () => {
    // The student answered items 0 and 2 and lost the connection on 1 — perhaps
    // by stepping back to change an answer. Resume must re-ask 1.
    expect(nextUnansweredIndex(order, ["i0", "i2"])).toBe(1);
  });

  it("resumes mid-module after a dropped connection", () => {
    expect(nextUnansweredIndex(order, ["i0", "i1", "i2"])).toBe(3);
  });

  it("reports past-the-end when everything is answered", () => {
    expect(nextUnansweredIndex(order, order.map((i) => i.id))).toBe(order.length);
  });

  it("accepts a Set as readily as an array", () => {
    expect(nextUnansweredIndex(order, new Set(["i0"]))).toBe(1);
  });

  it("ignores answers to items that are no longer in the assessment", () => {
    // An instrument reloaded with different codes should not fake progress.
    expect(nextUnansweredIndex(order, ["deleted-item"])).toBe(0);
  });
});

describe("the progress path", () => {
  const modules = groupIntoModules(items(3, 2));

  it("counts per module rather than one global bar", () => {
    const progress = progressForModules(modules, ["i0", "i1"], 2);

    expect(progress).toEqual([
      { code: "interests", total: 3, answered: 2, currentIndex: 2, complete: false },
      { code: "personality", total: 2, answered: 0, currentIndex: null, complete: false },
    ]);
  });

  it("marks a finished module complete and moves the position on", () => {
    const progress = progressForModules(modules, ["i0", "i1", "i2"], 3);

    expect(progress[0].complete).toBe(true);
    expect(progress[0].currentIndex).toBeNull();
    expect(progress[1].currentIndex).toBe(0);
  });

  it("leaves no module current once the assessment is finished", () => {
    const answered = flattenModules(modules).map((i) => i.id);

    const progress = progressForModules(modules, answered, 5);

    expect(progress.every((m) => m.complete)).toBe(true);
    expect(progress.every((m) => m.currentIndex === null)).toBe(true);
  });
});

describe("module boundaries", () => {
  const modules = groupIntoModules(items(3, 2));

  it("locates an index inside the first module", () => {
    expect(moduleIndexForItem(modules, 0)).toBe(0);
    expect(moduleIndexForItem(modules, 2)).toBe(0);
  });

  it("locates the first index of the second module", () => {
    // The transition screen (§13) is shown on this step and no other.
    expect(moduleIndexForItem(modules, 3)).toBe(1);
  });

  it("clamps past the end to the last module", () => {
    expect(moduleIndexForItem(modules, 99)).toBe(1);
  });
});

describe("timing", () => {
  it("keeps an ordinary answer intact", () => {
    expect(clampMsElapsed(2400)).toBe(2400);
  });

  it("caps an item left open over a lunch break", () => {
    // Uncapped, this would add an hour to the sum flags.py compares against six
    // minutes, hiding a session that was actually rushed.
    expect(clampMsElapsed(3_600_000)).toBe(MAX_MS_ELAPSED);
  });

  it("treats nonsense as no measurement rather than passing it to Postgres", () => {
    // Zero, not the cap: Infinity and NaN are a broken measurement, not a long
    // one, so they belong with "we do not know" rather than with "an hour".
    // Zero is already this codebase's no-measurement value — `record_response`
    // does `coalesce(p_ms_elapsed, 0)` for the nullable column.
    expect(clampMsElapsed(-1)).toBe(0);
    expect(clampMsElapsed(Number.NaN)).toBe(0);
    expect(clampMsElapsed(Number.POSITIVE_INFINITY)).toBe(0);
  });

  it("rounds, because ms_elapsed is an int column", () => {
    expect(clampMsElapsed(1200.7)).toBe(1201);
  });
});

describe("validating an answer", () => {
  const interest = item({ id: "i", response_min: 0, response_max: 1 });
  const personality = item({ id: "p", response_min: 1, response_max: 5 });

  it("accepts both ends of each range", () => {
    expect(isValidResponse(interest, 0)).toBe(true);
    expect(isValidResponse(interest, 1)).toBe(true);
    expect(isValidResponse(personality, 1)).toBe(true);
    expect(isValidResponse(personality, 5)).toBe(true);
  });

  it("rejects a personality value on the interests scale", () => {
    expect(isValidResponse(interest, 4)).toBe(false);
  });

  it("rejects zero for a 1..5 item", () => {
    // The off-by-one that would silently shift a whole domain's raw sum.
    expect(isValidResponse(personality, 0)).toBe(false);
  });

  it("rejects non-integers", () => {
    expect(isValidResponse(personality, 3.5)).toBe(false);
    expect(isValidResponse(personality, Number.NaN)).toBe(false);
  });
});
