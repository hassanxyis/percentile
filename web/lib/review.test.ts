import { describe, expect, it } from "vitest";

import {
  barPercent,
  flagMeaning,
  hexagonPoints,
  interestBandFlag,
  isOverdue,
  isProminentFlag,
  REVIEW_BACKLOG_DAYS,
  waitingFor,
  warnCount,
  type QualityFlag,
} from "@/lib/review";

/**
 * M8's "done when" is a walk-through with a real psychologist and a real
 * session, which needs a database. What can be proved without one is the part
 * that decides what a reviewer *sees*: that every flag the engine can emit has
 * a plain-language meaning (§9.1), and that the chart arithmetic is right.
 *
 * The flag-coverage test is the one that matters most. §9.1 requires every
 * quality flag to carry its meaning, and the failure mode is silent — a flag
 * added to `flags.py` with no entry here renders beside a blank explanation,
 * which reads as "nothing to worry about".
 */

function flag(overrides: Partial<QualityFlag> = {}): QualityFlag {
  return { code: "straightlining", severity: "warn", detail: "12 in a row", ...overrides };
}

describe("flagMeaning", () => {
  /**
   * Every code `engine/app/scoring/flags.py` can produce. Kept as a literal
   * list rather than imported, because the point is to notice when the two
   * drift: a new flag in Python should fail here until someone writes the
   * sentence a psychologist reads.
   */
  const ENGINE_FLAG_CODES = [
    "straightlining",
    "too_fast",
    "inconsistent_pairs",
    "long_gap",
    "get2_uniform_response",
  ];

  it.each(ENGINE_FLAG_CODES)("explains %s in plain language", (code) => {
    const meaning = flagMeaning(code);

    expect(meaning.length).toBeGreaterThan(20);
    // The fallback would technically "explain" every code. Assert we did not
    // get it.
    expect(meaning).not.toContain("See the detail beside it");
  });

  it("says something rather than nothing for an unknown code", () => {
    expect(flagMeaning("invented_later")).toBeTruthy();
  });

  it("does not interpret the student (R3, R7)", () => {
    // R7 forbids clinical language anywhere a reviewer or student reads, and R3
    // reserves interpretation for a human. These strings describe what was
    // measured, never what it says about the person.
    const forbidden = /\b(disorder|diagnos|patholog|abnormal|deficien|lazy|dishonest)/i;

    for (const code of ENGINE_FLAG_CODES) {
      expect(flagMeaning(code)).not.toMatch(forbidden);
    }
  });
});

describe("isProminentFlag", () => {
  it("promotes warns", () => {
    expect(isProminentFlag(flag({ severity: "warn" }))).toBe(true);
  });

  it("leaves info-level flags alone", () => {
    expect(isProminentFlag(flag({ code: "long_gap", severity: "info" }))).toBe(false);
  });

  it("promotes undifferentiated, which §9.1 names beside straightlining", () => {
    expect(isProminentFlag(flag({ code: "undifferentiated", severity: "info" }))).toBe(true);
  });
});

describe("warnCount", () => {
  it("counts only warns — two exclude a participant from cohort aggregates", () => {
    expect(
      warnCount([
        flag({ severity: "warn" }),
        flag({ code: "long_gap", severity: "info" }),
        flag({ code: "too_fast", severity: "warn" }),
      ]),
    ).toBe(2);
  });

  it("is zero for a clean session", () => {
    expect(warnCount([])).toBe(0);
  });
});

describe("interestBandFlag", () => {
  it("raises a flag for an undifferentiated profile", () => {
    expect(interestBandFlag("undifferentiated")).toMatchObject({
      code: "undifferentiated",
      severity: "warn",
    });
  });

  it("is null for any other band, including a missing one", () => {
    expect(interestBandFlag("moderate")).toBeNull();
    expect(interestBandFlag("well_differentiated")).toBeNull();
    expect(interestBandFlag(null)).toBeNull();
    expect(interestBandFlag(undefined)).toBeNull();
  });
});

describe("hexagonPoints", () => {
  it("puts the first scale at the top", () => {
    const points = hexagonPoints([10, 0, 0, 0, 0, 0], 10, 100, 100).split(" ");

    // Full value on scale one: straight up from the centre.
    expect(points[0]).toBe("100,0");
  });

  it("scales against the instrument ceiling, not the observed maximum", () => {
    // A flat profile at half the ceiling must render at half radius, not full.
    // Scaling to the observed max would make every profile look equally strong.
    const points = hexagonPoints([5, 5, 5, 5, 5, 5], 10, 100, 100).split(" ");

    expect(points[0]).toBe("100,50");
  });

  it("clamps a value above the ceiling instead of drawing outside the chart", () => {
    const points = hexagonPoints([99, 0, 0, 0, 0, 0], 10, 100, 100).split(" ");

    expect(points[0]).toBe("100,0");
  });

  it("collapses to the centre when the ceiling is zero", () => {
    // An empty items table (R2) can produce this. It must not divide by zero.
    const points = hexagonPoints([0, 0, 0, 0, 0, 0], 0, 100, 100).split(" ");

    expect(points).toHaveLength(6);
    expect(points[0]).toBe("100,100");
  });

  it("emits one point per scale", () => {
    expect(hexagonPoints([1, 2, 3, 4, 5, 6], 10, 50, 50).split(" ")).toHaveLength(6);
  });
});

describe("barPercent", () => {
  it("maps a domain onto its own range, not onto zero", () => {
    // IPIP domains run 10..50, so the floor is 10 and it must read as 0%.
    expect(barPercent(10, 10, 50)).toBe(0);
    expect(barPercent(30, 10, 50)).toBe(50);
    expect(barPercent(50, 10, 50)).toBe(100);
  });

  it("clamps rather than overflowing its bar", () => {
    expect(barPercent(99, 10, 50)).toBe(100);
    expect(barPercent(0, 10, 50)).toBe(0);
  });

  it("returns zero for a degenerate range", () => {
    expect(barPercent(5, 10, 10)).toBe(0);
  });
});

describe("waitingFor", () => {
  const now = new Date("2026-09-05T12:00:00Z");

  it("reads coarsely, in the units a reviewer thinks in", () => {
    expect(waitingFor("2026-09-05T11:30:00Z", now)).toBe("just now");
    expect(waitingFor("2026-09-05T09:00:00Z", now)).toBe("3 hours");
    expect(waitingFor("2026-09-05T11:00:00Z", now)).toBe("1 hour");
    expect(waitingFor("2026-09-01T12:00:00Z", now)).toBe("4 days");
    expect(waitingFor("2026-09-04T12:00:00Z", now)).toBe("1 day");
  });

  it("does not say a session has waited a negative time", () => {
    // Clock skew between Postgres and the renderer. "in 1 hour" beside a
    // waiting student is absurd.
    expect(waitingFor("2026-09-05T13:00:00Z", now)).toBe("just now");
  });

  it("handles a session with no submission time", () => {
    expect(waitingFor(null, now)).toBe("—");
    expect(waitingFor("not a date", now)).toBe("—");
  });
});

describe("isOverdue", () => {
  const now = new Date("2026-09-05T12:00:00Z");

  it("fires at the §9.4 threshold", () => {
    const fiveDaysAgo = new Date(
      now.getTime() - REVIEW_BACKLOG_DAYS * 24 * 3_600_000,
    ).toISOString();

    expect(isOverdue(fiveDaysAgo, now)).toBe(true);
    expect(isOverdue("2026-09-04T12:00:00Z", now)).toBe(false);
  });

  it("is false when there is nothing to measure", () => {
    expect(isOverdue(null, now)).toBe(false);
    expect(isOverdue("not a date", now)).toBe(false);
  });
});
