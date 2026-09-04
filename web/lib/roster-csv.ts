/**
 * Roster CSV parsing and validation (plan.md §12, M5).
 *
 * Pure: text in, result out. No I/O, no Supabase, no Next imports — which is
 * what lets `roster-csv.test.ts` cover M5's "done when" (a 20-row CSV imports
 * cleanly with per-row validation errors shown) without a database or a browser.
 *
 * Two deliberate departures from `engine/scripts/load_instruments.py`, which is
 * otherwise the model for this file:
 *
 *   - **Every error is collected, not the first.** That script raises on the
 *     first bad row because a malformed instrument file is a transcription bug
 *     with one correct fix. A roster is a document a human typed, and handing
 *     back one error at a time turns a 20-row file into 20 upload cycles.
 *   - **Row numbers are spreadsheet line numbers**, header being 1. The
 *     counsellor is going to fix this in Excel, so the number has to match what
 *     Excel shows in the gutter.
 *
 * The format is defined *here* rather than inherited: plan.md v2 says the roster
 * CSV is "unchanged from v1 §12" and the v1 document is not in this repository.
 */

import { INTENDED_FIELDS_SENTENCE, isIntendedField } from "@/lib/intended-fields";

export const EDUCATION_LEVELS = ["matric", "intermediate", "undergraduate"] as const;
export type EducationLevel = (typeof EDUCATION_LEVELS)[number];

export const REQUIRED_COLUMNS = ["full_name", "email"] as const;
export const OPTIONAL_COLUMNS = [
  "external_ref",
  "intended_field",
  "education_level",
] as const;

/** Row limit. §9.3 sizes a pilot cohort at 15–25 students; 500 is far above any
 *  real class list and exists only to stop a mis-selected file from being
 *  parsed row by row. */
export const MAX_ROWS = 500;

export type RosterRow = {
  full_name: string;
  email: string;
  external_ref: string | null;
  intended_field: string | null;
  education_level: EducationLevel | null;
};

export type RowError = {
  /** Spreadsheet line number: header is 1, first data row is 2. */
  row: number;
  column: string;
  message: string;
};

export type ParseResult =
  | { ok: true; rows: RosterRow[] }
  | { ok: false; errors: RowError[] };

/**
 * Split one CSV line, honouring double-quoted fields.
 *
 * Hand-written rather than a dependency: the grammar needed here is quoted
 * fields, embedded commas, and doubled quotes as an escape. A school exporting
 * "Khan, Fatima" from Excel is the case this exists for.
 */
function splitLine(line: string): string[] {
  const fields: string[] = [];
  let field = "";
  let inQuotes = false;

  for (let i = 0; i < line.length; i++) {
    const char = line[i];

    if (inQuotes) {
      if (char === '"') {
        if (line[i + 1] === '"') {
          field += '"'; // "" inside quotes is a literal quote
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += char;
      }
      continue;
    }

    if (char === '"') {
      inQuotes = true;
    } else if (char === ",") {
      fields.push(field);
      field = "";
    } else {
      field += char;
    }
  }

  fields.push(field);
  return fields;
}

/**
 * Accepts anything with one `@`, non-empty on both sides and no whitespace.
 *
 * Deliberately loose. The authoritative test of an address is whether mail
 * arrives, and a stricter pattern's failure mode is rejecting a real student's
 * real address — which a counsellor cannot fix and cannot work around.
 */
function looksLikeEmail(value: string): boolean {
  if (/\s/.test(value)) return false;
  const parts = value.split("@");
  if (parts.length !== 2) return false;
  const [local, domain] = parts;
  return local.length > 0 && domain.includes(".") && !domain.startsWith(".") &&
    !domain.endsWith(".");
}

export function parseRosterCsv(text: string): ParseResult {
  // Excel on Windows writes a BOM, and every school will export from Excel.
  const withoutBom = text.replace(/^﻿/, "");
  const lines = withoutBom.split(/\r\n|\n|\r/);

  const headerIndex = lines.findIndex((line) => line.trim() !== "");
  if (headerIndex === -1) {
    return { ok: false, errors: [{ row: 1, column: "file", message: "The file is empty." }] };
  }

  const header = splitLine(lines[headerIndex]).map((h) => h.trim().toLowerCase());
  const missing = REQUIRED_COLUMNS.filter((column) => !header.includes(column));
  if (missing.length > 0) {
    // A missing column is a file-level problem: every row would fail for the
    // same reason, so reporting it once is clearer than reporting it 20 times.
    return {
      ok: false,
      errors: [
        {
          row: 1,
          column: missing.join(", "),
          message:
            `The file needs a ${missing.join(" and ")} column. ` +
            `Found: ${header.filter(Boolean).join(", ") || "nothing"}.`,
        },
      ],
    };
  }

  const columnAt = (name: string) => header.indexOf(name);
  const errors: RowError[] = [];
  const rows: RosterRow[] = [];
  const seenEmails = new Map<string, number>();

  for (let i = headerIndex + 1; i < lines.length; i++) {
    const raw = lines[i];
    if (raw.trim() === "") continue; // trailing newline, or a spacer row

    const lineNumber = i + 1; // 0-indexed array -> 1-indexed spreadsheet gutter

    if (rows.length >= MAX_ROWS) {
      errors.push({
        row: lineNumber,
        column: "file",
        message: `More than ${MAX_ROWS} rows. Is this the right file?`,
      });
      break;
    }

    const fields = splitLine(raw);
    const valueOf = (name: string): string => {
      const index = columnAt(name);
      return index === -1 ? "" : (fields[index] ?? "").trim();
    };

    const fullName = valueOf("full_name");
    const email = valueOf("email").toLowerCase();
    const externalRef = valueOf("external_ref");
    const intendedField = valueOf("intended_field").toLowerCase();
    const educationLevel = valueOf("education_level").toLowerCase();

    const before = errors.length;

    if (!fullName) {
      errors.push({ row: lineNumber, column: "full_name", message: "Name is missing." });
    }

    if (!email) {
      // Nullable in the schema, required here: without an address there is
      // nobody to invite, and the invite is the point of the row.
      errors.push({ row: lineNumber, column: "email", message: "Email is missing." });
    } else if (!looksLikeEmail(email)) {
      errors.push({
        row: lineNumber,
        column: "email",
        message: `"${email}" does not look like an email address.`,
      });
    } else {
      const firstSeen = seenEmails.get(email);
      if (firstSeen !== undefined) {
        errors.push({
          row: lineNumber,
          column: "email",
          message: `${email} is already on row ${firstSeen}.`,
        });
      } else {
        seenEmails.set(email, lineNumber);
      }
    }

    if (intendedField && !isIntendedField(intendedField)) {
      errors.push({
        row: lineNumber,
        column: "intended_field",
        message: `"${intendedField}" is not one of: ${INTENDED_FIELDS_SENTENCE}.`,
      });
    }

    if (
      educationLevel &&
      !(EDUCATION_LEVELS as readonly string[]).includes(educationLevel)
    ) {
      errors.push({
        row: lineNumber,
        column: "education_level",
        message: `"${educationLevel}" is not one of: ${EDUCATION_LEVELS.join(", ")}.`,
      });
    }

    if (errors.length === before) {
      rows.push({
        full_name: fullName,
        email,
        external_ref: externalRef || null,
        intended_field: intendedField || null,
        education_level: (educationLevel || null) as EducationLevel | null,
      });
    }
  }

  if (rows.length === 0 && errors.length === 0) {
    return {
      ok: false,
      errors: [{ row: 1, column: "file", message: "The file has a header but no students." }],
    };
  }

  // All-or-nothing. A partial import of 17 of 20 leaves the counsellor working
  // out which three are missing, and the obvious fix — correct the file and
  // upload it again — then trips the duplicate check on the 17 that landed.
  if (errors.length > 0) {
    return { ok: false, errors };
  }

  return { ok: true, rows };
}

/** The template offered on the upload screen. */
export const ROSTER_TEMPLATE = [
  [...REQUIRED_COLUMNS, ...OPTIONAL_COLUMNS].join(","),
  "Fatima Khan,fatima.khan@example.edu.pk,A-001,medicine,intermediate",
  "Hamza Ali,hamza.ali@example.edu.pk,A-002,engineering,intermediate",
].join("\n");
