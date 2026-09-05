import { type EmailOtpType } from "@supabase/supabase-js";
import { NextResponse, type NextRequest } from "next/server";

import { createClient } from "@/lib/supabase/server";

/**
 * /auth/confirm — where an emailed auth link lands (plan §13).
 *
 * This is how an invited counsellor or psychologist gets a session in *this*
 * app's cookies. Without it, an invitation ends on /login with no session, which
 * reads to the recipient as "it sent me to the sign-in page instead of letting
 * me in".
 *
 * **`verifyOtp`, not `exchangeCodeForSession`.** The distinction is not
 * cosmetic and it is the reason this route exists in this shape:
 *
 *   - `?code=` + `exchangeCodeForSession` is the OAuth/PKCE flow — social login.
 *   - Email links (invite, recovery, signup, email change) carry a **token
 *     hash**, and Supabase's own server-side examples verify it with
 *     `verifyOtp({ type, token_hash })`.
 *
 * A default `{{ .ConfirmationURL }}` link goes to Supabase's `/auth/v1/verify`,
 * which redirects back with the tokens in the **URL fragment** (`#access_token=`).
 * A fragment is never sent to the server, so a route handler cannot read it —
 * that is the documented limitation this pattern exists to work around. Hence
 * the requirement below.
 *
 * ─────────────────────────────────────────────────────────────────────────
 * REQUIRES A DASHBOARD CHANGE. The "Invite user" template (Authentication →
 * Email Templates) must point here rather than at the default:
 *
 *   <a href="{{ .SiteURL }}/auth/confirm?token_hash={{ .TokenHash }}&type=invite&next=/auth/set-password">
 *     Accept the invitation
 *   </a>
 *
 * Until that is saved, invitations still land on /login with no session. The
 * code here cannot detect or repair that; `{{ .TokenHash }}` is only present if
 * the template asks for it.
 * ─────────────────────────────────────────────────────────────────────────
 */

/**
 * Link types this app actually sends. An allowlist rather than a cast: `type`
 * is attacker-controllable query text, and handing an arbitrary string to
 * `verifyOtp` would let a crafted link exercise flows (`email_change`, `sms`)
 * that nothing here issues.
 */
const ALLOWED_TYPES: readonly EmailOtpType[] = ["invite", "recovery", "email"] as const;

/** Where an invited member goes once verified: they still have no password. */
const DEFAULT_NEXT = "/auth/set-password";

export async function GET(request: NextRequest) {
  const { searchParams, origin } = new URL(request.url);

  const tokenHash = searchParams.get("token_hash");
  const type = searchParams.get("type");
  const next = searchParams.get("next");

  // Relative paths only. An absolute `next` would turn an emailed link into an
  // open redirect — and this one arrives carrying a valid credential, so the
  // usual "it only redirects" reassurance does not apply. `//evil.test` is
  // rejected too: it is protocol-relative, not a path on this origin.
  const safeNext =
    next && next.startsWith("/") && !next.startsWith("//") ? next : DEFAULT_NEXT;

  if (!tokenHash || !type || !ALLOWED_TYPES.includes(type as EmailOtpType)) {
    return NextResponse.redirect(new URL("/login?error=bad-link", origin));
  }

  const supabase = await createClient();
  const { error } = await supabase.auth.verifyOtp({
    type: type as EmailOtpType,
    token_hash: tokenHash,
  });

  if (error) {
    // Overwhelmingly this is an expired link — invitations last one hour by
    // default (the project's Email OTP Expiration), and a re-used one is spent.
    // Neither is recoverable here; an org_admin has to invite again.
    console.error("auth confirm failed", { type, code: error.code, message: error.message });
    return NextResponse.redirect(new URL("/login?error=link-expired", origin));
  }

  return NextResponse.redirect(new URL(safeNext, origin));
}
