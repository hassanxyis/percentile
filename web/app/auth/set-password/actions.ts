"use server";

import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";

import { createClient } from "@/lib/supabase/server";

/**
 * Set a password on an account that does not have one yet (plan §13).
 *
 * **Supabase does not host a set-password page.** `inviteUserByEmail` creates
 * the auth user and mails a link; accepting that link proves the address and
 * establishes a session, and that is all it does. The account still has no
 * password — and `login/actions.ts` only ever calls `signInWithPassword`,
 * because accounts are invite-only and there is no magic-link path for staff.
 *
 * So without this action an invited colleague can open the app exactly once,
 * from the emailed link, and can never sign in again. That is the gap this
 * closes.
 *
 * `updateUser` acts on the caller's own session and nothing else. It takes no
 * user id, so this cannot be pointed at another account — which is why it is
 * safe here despite every other write in the app going through the service role.
 */

export type SetPasswordState = { error?: string } | undefined;

// Supabase rejects anything under 6 by default. Asking for 8 is a deliberate
// step up: these accounts read minors' assessment data, and `psychologist`
// accounts read interview notes that §16 says to treat like health data.
const MIN_LENGTH = 8;

export async function setPassword(
  _state: SetPasswordState,
  formData: FormData,
): Promise<SetPasswordState> {
  const password = String(formData.get("password") ?? "");
  const confirmation = String(formData.get("confirm_password") ?? "");

  if (password.length < MIN_LENGTH) {
    return { error: `Use at least ${MIN_LENGTH} characters.` };
  }
  if (password !== confirmation) {
    return { error: "Those two passwords are not the same." };
  }

  const supabase = await createClient();

  // The session comes from the emailed link (`/auth/confirm` → verifyOtp), so
  // an expired or already-used link lands here with nothing. Checked again in
  // the action rather than trusting the page guard: a server action is
  // reachable by direct POST.
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) {
    return { error: "That link has expired. Ask for a fresh invitation." };
  }

  const { error } = await supabase.auth.updateUser({ password });

  if (error) {
    // Supabase's own rules can still refuse it — a leaked-password check, or a
    // project minimum longer than ours. Its message is written for an end user,
    // so it is worth showing rather than replacing with something vaguer.
    console.error("set password failed", { code: error.code, message: error.message });
    return { error: error.message || "That password could not be saved." };
  }

  revalidatePath("/", "layout");
  redirect("/dash");
}
