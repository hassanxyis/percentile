import { LoginForm } from "./login-form";

/**
 * /login (plan §13).
 *
 * Accounts are invite-only, so there is no "create an account" link — an
 * institution's first admin is set up by hand, and everyone else is invited
 * from /dash/settings.
 */
export default async function LoginPage({ searchParams }: PageProps<"/login">) {
  const params = await searchParams;
  const rawNext = params.next;
  const next = typeof rawNext === "string" && rawNext.startsWith("/") ? rawNext : "/dash";
  const noProfile = params.error === "no-profile";

  return (
    <main className="mx-auto flex w-full max-w-sm flex-col gap-6 px-6 py-24">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">Sign in</h1>
        <p className="text-sm text-black/60 dark:text-white/60">
          For counsellors and reviewers. Students receive a link by email and do
          not need an account.
        </p>
      </div>

      {noProfile && (
        <div
          role="alert"
          className="flex flex-col gap-2 rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm"
        >
          <p>
            That account is not attached to an institution yet. Ask your
            administrator to finish the invitation.
          </p>
          {/* The session is still valid, so signing out is the only way off this
              screen — otherwise the visitor is stuck with a working login and
              nowhere to go. */}
          <form action="/logout" method="post">
            <button type="submit" className="underline">
              Sign out
            </button>
          </form>
        </div>
      )}

      <LoginForm next={next} />
    </main>
  );
}
