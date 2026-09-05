import { NextResponse } from "next/server";

import { createClient } from "@/lib/supabase/server";

/**
 * /auth/callback — where Supabase sends the browser after an invite is accepted
 * (or a magic link / recovery completes), so the one-time `code` can be
 * exchanged for a session and stored in this app's cookies.
 *
 * There was no such route before; without it an invited member who set a
 * password on Supabase's hosted page came back to the app with no session and
 * had to sign in by hand — the "landed on the login page" feel.
 *
 * `inviteMember` (app/dash/settings/actions.ts) points the invite's
 * `redirectTo` here with `?next=/dash`; SITE_URL in the Supabase dashboard is
 * the fallback for anything that does not carry the query param.
 */

const DEFAULT_NEXT = "/dash";

export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url);
  const code = searchParams.get("code");
  const next = searchParams.get("next");
  const safeNext = next && next.startsWith("/") ? next : DEFAULT_NEXT;

  const supabase = await createClient();

  if (code) {
    // PKCE flow: exchange the one-time code for a session.
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) {
      return NextResponse.redirect(`${origin}${safeNext}`);
    }
  } else {
    // Non-PKCE flow: GoTrue set the session cookies itself (or the tokens came
    // back in the URL fragment on the previous request). Proving the user is
    // known refreshes/confirms those cookies before we send them on.
    const {
      data: { user },
    } = await supabase.auth.getUser();
    if (user) {
      return NextResponse.redirect(`${origin}${safeNext}`);
    }
  }

  // Something went wrong exchanging the code. Send them to sign in; the
  // password they set on Supabase's page still works.
  return NextResponse.redirect(new URL("/login", origin));
}