import { describe, expect, it } from "vitest";

import { MAX_ROWS, parseRosterCsv, ROSTER_TEMPLATE } from "@/lib/roster-csv";

/**
 * M5's "done when" (plan.md §17): *a 20-row CSV imports cleanly with per-row
 * validation errors shown*. Both halves are asserted here — the clean import
 * and, more importantly, that a file with several problems reports all of them
 * at once rather than one per upload.
 */

const HEADER = "full_name,email,external_ref,intended_field,education_level";

function row(n: number): string {
  return `Student ${n},student${n}@example.edu.pk,A-${String(n).padStart(3, "0")},medicine,intermediate`;
}

function file(...lines: string[]): string {
  return [HEADER, ...lines].join("\n");
}

describe("a clean file", () => {
  it("imports 20 rows", () => {
    const csv = file(...Array.from({ length: 20 }, (_, i) => row(i + 1)));

    const result = parseRosterCsv(csv);

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.rows).toHaveLength(20);
    expect(result.rows[0]).toEqual({
      full_name: "Student 1",
      email: "student1@example.edu.pk",
      external_ref: "A-001",
      intended_field: "medicine",
      education_level: "intermediate",
    });
  });

  it("accepts the template we hand out", () => {
    const result = parseRosterCsv(ROSTER_TEMPLATE);

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.rows).toHaveLength(2);
  });

  it("treats the optional columns as optional", () => {
    const result = parseRosterCsv("full_name,email\nFatima Khan,fatima@example.edu.pk");

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.rows[0].external_ref).toBeNull();
    expect(result.rows[0].intended_field).toBeNull();
    expect(result.rows[0].education_level).toBeNull();
  });
});

describe("what Excel does to a file", () => {
  it("survives a BOM", () => {
    // Excel on Windows writes one, and every school will export from Excel.
    const result = parseRosterCsv("﻿" + file(row(1)));

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.rows[0].full_name).toBe("Student 1");
  });

  it("survives CRLF line endings", () => {
    const result = parseRosterCsv([HEADER, row(1), row(2)].join("\r\n"));

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.rows).toHaveLength(2);
  });

  it("skips blank lines, including a trailing newline", () => {
    const result = parseRosterCsv(file(row(1), "", row(2), "") + "\n");

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.rows).toHaveLength(2);
  });

  it("reads a quoted field containing a comma", () => {
    const result = parseRosterCsv(
      'full_name,email\n"Khan, Fatima",fatima@example.edu.pk',
    );

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.rows[0].full_name).toBe("Khan, Fatima");
  });

  it("reads a doubled quote as a literal quote", () => {
    const result = parseRosterCsv(
      'full_name,email\n"Fatima ""Fati"" Khan",fatima@example.edu.pk',
    );

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.rows[0].full_name).toBe('Fatima "Fati" Khan');
  });

  it("does not care what order the columns are in", () => {
    const result = parseRosterCsv("email,full_name\nfatima@example.edu.pk,Fatima Khan");

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.rows[0].full_name).toBe("Fatima Khan");
  });

  it("ignores columns it does not recognise", () => {
    // A school exporting from its own MIS will have extras.
    const result = parseRosterCsv(
      "full_name,email,house,guardian_phone\nFatima Khan,fatima@example.edu.pk,Blue,0300",
    );

    expect(result.ok).toBe(true);
  });
});

describe("row-level errors", () => {
  it("reports every bad row at once, not just the first", () => {
    // The heart of M5's done-when. A counsellor fixing a 20-row file one error
    // per upload is the failure this test exists to prevent.
    const csv = file(
      row(1),
      "No Email Student,,A-002,medicine,intermediate",
      row(3),
      ",missing.name@example.edu.pk,A-004,medicine,intermediate",
      row(5),
      "Bad Field,bad.field@example.edu.pk,A-006,astrology,intermediate",
    );

    const result = parseRosterCsv(csv);

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.errors).toHaveLength(3);
    expect(result.errors.map((e) => e.column)).toEqual([
      "email",
      "full_name",
      "intended_field",
    ]);
  });

  it("numbers rows the way the spreadsheet does", () => {
    // Header is line 1, so the first data row is 2. The counsellor is going to
    // fix this in Excel and needs the number in the gutter to match.
    const result = parseRosterCsv(file(row(1), "Broken,not-an-email,A-003,,"));

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.errors[0].row).toBe(3);
  });

  it("rejects an address with no @", () => {
    const result = parseRosterCsv(file("Fatima Khan,fatima.example.edu.pk,,,"));

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.errors[0].column).toBe("email");
  });

  it("rejects an address with a space in it", () => {
    const result = parseRosterCsv(file("Fatima Khan,fatima khan@example.edu.pk,,,"));

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.errors[0].column).toBe("email");
  });

  it("rejects an unknown education level", () => {
    const result = parseRosterCsv(file("Fatima Khan,fatima@example.edu.pk,,,olevel"));

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.errors[0].column).toBe("education_level");
  });

  it("points at the earlier row when an email repeats", () => {
    const result = parseRosterCsv(
      file(
        "Fatima Khan,fatima@example.edu.pk,A-001,,",
        "Fatima K,fatima@example.edu.pk,A-002,,",
      ),
    );

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.errors).toHaveLength(1);
    expect(result.errors[0].row).toBe(3);
    expect(result.errors[0].message).toContain("row 2");
  });

  it("treats addresses differing only in case as the same student", () => {
    const result = parseRosterCsv(
      file("Fatima Khan,Fatima@Example.edu.pk,,,", "Fatima K,fatima@example.edu.pk,,,"),
    );

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.errors[0].column).toBe("email");
  });

  it("imports nothing when any row is bad", () => {
    // All-or-nothing: a partial import leaves the counsellor reconciling which
    // rows landed, and re-uploading the corrected file then trips the
    // duplicate check on the ones that did.
    const result = parseRosterCsv(file(row(1), row(2), "Broken,,,,"));

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result).not.toHaveProperty("rows");
  });
});

describe("file-level errors", () => {
  it("names the column that is missing", () => {
    const result = parseRosterCsv("full_name,external_ref\nFatima Khan,A-001");

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.errors).toHaveLength(1); // once, not once per row
    expect(result.errors[0].message).toContain("email");
  });

  it("rejects an empty file", () => {
    const result = parseRosterCsv("");

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.errors[0].message).toContain("empty");
  });

  it("rejects a header with no students under it", () => {
    const result = parseRosterCsv(HEADER + "\n\n");

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.errors[0].message).toContain("no students");
  });

  it("stops on an implausibly large file", () => {
    const csv = file(...Array.from({ length: MAX_ROWS + 10 }, (_, i) => row(i + 1)));

    const result = parseRosterCsv(csv);

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.errors.at(-1)?.message).toContain(String(MAX_ROWS));
  });
});
