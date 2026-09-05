"use server";

import { createHash, randomBytes } from "node:crypto";
import { revalidatePath } from "next/cache";

import { requireRole } from "@/lib/dal";
import { parseRosterCsv, type RowError } from "@/lib/roster-csv";
import { createAdminClient } from "@/lib/supabase/admin";

/**
 * Roster import (plan.md §12, §13, M5).
 *
 * Follows the rules in `app/dash/actions.ts`: `requireRole` first, the admin
 * client for writes, and `organisation_id` taken from the session rather than
 * the request. Here that matters more than usual — the import writes rows for
 * minors into a named school, so a form-supplied organisation would be a
 * cross-tenant write.
 */

export type ImportedInvite = {
  full_name: string;
  email: string;
  invite_url: string;
};

export type ImportState =
  | { status: "idle" }
  | { status: "error"; message: string }
  | { status: "invalid"; errors: RowError[] }
  | { status: "done"; count: number; invites: ImportedInvite[] };

/**
 * A 256-bit token, base64url so it survives a URL without escaping.
 *
 * This token *is* the student's authentication. `0002_rls.sql` is explicit that
 * students are never authenticated and the taker flow resolves
 * `sha256(token) → participants.invite_token_hash`, so anyone holding it holds
 * that student's session. Hence `randomBytes`, never `Math.random`, and 32
 * bytes rather than something merely long enough to look unguessable.
 */
function mintToken(): { token: string; hash: string } {
  const token = randomBytes(32).toString("base64url");
  return { token, hash: createHash("sha256").update(token).digest("hex") };
}

/**
 * Which of these emails are already on the cohort.
 *
 * Only called after a 23505, to turn "some of these students" into their actual
 * names. A counsellor looking at a 40-row file needs to know which rows to
 * remove; the generic message leaves them comparing two lists by hand.
 */
async function findExistingEmails(
  admin: ReturnType<typeof createAdminClient>,
  cohortId: string,
  emails: string[],
): Promise<{ full_name: string; email: string | null }[]> {
  const { data } = await admin
    .from("participants")
    .select("full_name, email")
    .eq("cohort_id", cohortId)
    .in("email", emails);

  return data ?? [];
}

/** "Fatima Khan", or "Fatima Khan and Hamza Ali", or "Fatima Khan, Hamza Ali and 3 others". */
function listNames(rows: { full_name: string }[]): string {
  const names = rows.map((row) => row.full_name);
  if (names.length === 1) return names[0];
  if (names.length === 2) return `${names[0]} and ${names[1]}`;
  if (names.length <= 4) {
    return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  }
  return `${names.slice(0, 3).join(", ")} and ${names.length - 3} others`;
}

export async function importRoster(
  _state: ImportState,
  formData: FormData,
): Promise<ImportState> {
  const session = await requireRole("counsellor", "org_admin", "superadmin");

  const cohortId = String(formData.get("cohort_id") ?? "");
  const file = formData.get("file");

  if (!cohortId) {
    return { status: "error", message: "Which cohort is this for?" };
  }
  if (!(file instanceof File) || file.size === 0) {
    return { status: "error", message: "Choose a CSV file to upload." };
  }

  const parsed = parseRosterCsv(await file.text());
  if (!parsed.ok) {
    return { status: "invalid", errors: parsed.errors };
  }

  // Held in memory only. The raw tokens are returned to the browser once, for
  // the one-time download, and never written anywhere — the database stores
  // sha256 alone.
  const invites: ImportedInvite[] = [];
  const rows = parsed.rows.map((row) => {
    const { token, hash } = mintToken();
    invites.push({
      full_name: row.full_name,
      email: row.email,
      invite_url: `${process.env.NEXT_PUBLIC_APP_URL ?? ""}/a/${token}`,
    });
    return { ...row, invite_token_hash: hash };
  });

  const admin = createAdminClient();

  // One call. `import_roster` writes participants, one send_email job each, and
  // the audit row inside a single transaction — see 0005_import_roster.sql for
  // why that atomicity is not optional here.
  const { data, error } = await admin.rpc("import_roster", {
    p_cohort_id: cohortId,
    p_organisation_id: session.organisationId, // session, never the form
    p_actor: session.userId,
    p_rows: rows,
  });

  if (error) {
    // 23505 is a unique violation. Since 0007 that means an email already on
    // this cohort's roster — `participants_cohort_email_unique`.
    //
    // Until 0007 this branch could not fire for a duplicated student: the only
    // unique column was `invite_token_hash`, which is freshly minted per import,
    // so re-uploading the same file inserted a second full set of participants
    // instead of being refused. That is how twenty students became forty during
    // M6's end-to-end test.
    if (error.code === "23505") {
      const duplicates = await findExistingEmails(
        admin,
        cohortId,
        parsed.rows.map((row) => row.email),
      );
      return {
        status: "error",
        message: duplicates.length
          ? `Nothing was imported. ${listNames(duplicates)} ${
              duplicates.length === 1 ? "is" : "are"
            } already on this roster.`
          : "Nothing was imported. Some of these students are already on this roster.",
      };
    }

    return {
      status: "error",
      message: "The import did not go through. Nothing was saved.",
    };
  }

  revalidatePath(`/dash/cohorts/${cohortId}`);
  return { status: "done", count: Number(data ?? rows.length), invites };
}
