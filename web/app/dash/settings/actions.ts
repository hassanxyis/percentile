"use server";

import { revalidatePath } from "next/cache";

import { ROLES, requireRole, type Role } from "@/lib/dal";
import { createAdminClient } from "@/lib/supabase/admin";
import type { ActionState } from "../actions";

/**
 * Member invitation (plan §1, §13).
 *
 * Accounts are invite-only. There is no signup page and no `handle_new_user`
 * trigger on `auth.users`, because both would have to *guess* two things that
 * decide access to minors' records:
 *
 *   - `organisation_id` — which school's students this person can see.
 *   - `role` — whether they can read `reviews.interview_notes`, the
 *     psychologist's clinical-adjacent notes that §16 says to treat with the
 *     same restriction as health data.
 *
 * So an org_admin invites, and both values come from the inviter's session and
 * a fixed allowlist rather than from anything the new user supplies.
 *
 * The very first admin of an institution has no UI at all: seed the row by
 * hand. For a product with two customers that is the right amount of tooling.
 */
export async function inviteMember(
  _state: ActionState,
  formData: FormData,
): Promise<ActionState> {
  const session = await requireRole("org_admin", "superadmin");

  const email = String(formData.get("email") ?? "").trim().toLowerCase();
  const fullName = String(formData.get("full_name") ?? "").trim();
  const role = String(formData.get("role") ?? "") as Role;

  if (!email || !fullName) {
    return { error: "Name and email are both needed." };
  }
  if (!ROLES.includes(role)) {
    return { error: "Choose a valid role." };
  }
  // Only a superadmin can mint another superadmin. Otherwise one compromised
  // org_admin account escalates to every tenant in the system.
  if (role === "superadmin" && session.role !== "superadmin") {
    return { error: "Only a superadmin can grant that role." };
  }

  const admin = createAdminClient();

  // Supabase mails the invitation, but there is **no hosted set-password page**
  // at the other end of it. Accepting the link proves the address and creates a
  // session; the account still has no password, and `login/actions.ts` only ever
  // calls signInWithPassword. So the invitee must land on our own
  // `/auth/set-password` or they can never sign in a second time.
  //
  // `redirectTo` is where GoTrue sends the browser after verification, and it
  // must also be listed under Authentication → URL Configuration → Redirect
  // URLs. If it is not, Supabase silently falls back to Site URL — which is the
  // "the invite dropped me on the sign-in page" symptom.
  //
  // Getting to `/auth/confirm` at all needs `{{ .TokenHash }}`, which comes from
  // the invite email template rather than from here; app/auth/confirm/route.ts
  // carries the template to paste and the reason it is required.
  const appUrl = (process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000").replace(
    /\/$/,
    "",
  );

  const { data, error: inviteError } = await admin.auth.admin.inviteUserByEmail(email, {
    redirectTo: `${appUrl}/auth/set-password`,
  });

  if (inviteError || !data.user) {
    return { error: "Could not send that invitation." };
  }

  const { error: profileError } = await admin.from("profiles").insert({
    id: data.user.id,
    organisation_id: session.organisationId, // the inviter's org, not a form field
    full_name: fullName,
    role,
  });

  if (profileError) {
    // The auth.users row now exists without a profile. verifySession() sends
    // such an account back to /login with an explanation rather than showing an
    // empty dashboard; re-inviting the same address repairs it.
    return { error: "Invited, but the profile could not be created. Try again." };
  }

  await admin.from("audit_log").insert({
    actor: session.userId,
    action: "profile.invited",
    subject: data.user.id,
    meta: { email, role, organisation_id: session.organisationId },
  });

  revalidatePath("/dash/settings");
  return { ok: `Invitation sent to ${email}.` };
}
