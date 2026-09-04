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
    // 23505 is a unique violation, which for this table means the email or the
    // roll number is already on this cohort's roster. Re-importing a corrected
    // file is the common cause, so say that rather than "database error".
    const isDuplicate = error.code === "23505";
    return {
      status: "error",
      message: isDuplicate
        ? "Some of these students are already on this roster. Remove them from the file and upload again."
        : "The import did not go through. Nothing was saved.",
    };
  }

  revalidatePath(`/dash/cohorts/${cohortId}`);
  return { status: "done", count: Number(data ?? rows.length), invites };
}
