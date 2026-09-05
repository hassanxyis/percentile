import { redirect } from "next/navigation";

import { createClient } from "@/lib/supabase/server";

import { SetPasswordForm } from "./set-password-form";

/**
 * /auth/set-password — the last step of accepting an invitation (plan §13).
 *
 * Reached from `/auth/confirm`, which verifies the emailed token and leaves a
 * session in this app's cookies. At that moment the account exists, the address
 * is proven, and there is no password on it — see `actions.ts` for why Supabase
 * does not supply this screen itself.
 *
 * Deliberately NOT behind `verifySession()`. That helper is the counsellor DAL:
 * it requires a `profiles` row and redirects to /login without one. An invited
 * member does have a profile (inviteMember writes it), but a *recovery* link
 * lands here too, and the guard that belongs on this page is only "do you hold a
 * session" — the password being set is the caller's own, and `updateUser` cannot
 * reach any other account.
 */
export default async function SetPasswordPage() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  // No session means the link expired, was already used, or someone navigated
  // here directly. /login explains the first two; it is also where an existing
  // member with a working password belongs.
  if (!user) {
    redirect("/login?error=link-expired");
  }

  return (
    <main className="mx-auto flex w-full max-w-sm flex-col gap-6 px-6 py-24">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">Choose a password</h1>
        <p className="text-sm text-black/60 dark:text-white/60">
          One last step. You will use this with{" "}
          <span className="font-medium">{user.email}</span> to sign in from now
          on.
        </p>
      </div>

      <SetPasswordForm />
    </main>
  );
}
