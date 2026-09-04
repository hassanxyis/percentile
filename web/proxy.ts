import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

/**
 * Proxy — what Next.js called Middleware before v16.
 *
 * The rename is the whole reason this file is not `middleware.ts`:
 * "The `middleware.js` file convention has been deprecated in Next.js 16 and
 * renamed to `proxy.js`" (node_modules/next/dist/docs/01-app/03-api-reference/
 * 03-file-conventions/middleware.md). The exported function is `proxy`, and
 * setting `runtime` in this file throws — it is Node.js by default now.
 *
 * Two jobs, both cheap:
 *
 *   1. Refresh the auth token and hand the refreshed cookie to both the server
 *      components downstream and the browser.
 *   2. Bounce obviously-unauthenticated requests away from /dash and /review.
 *
 * This is not the security boundary. It runs on prefetches, so it must not
 * query the database; it reads the session cookie and nothing more. Real
 * authorisation happens in lib/dal.ts, next to the data, and ultimately in the
 * RLS policies.
 */

const PROTECTED_PREFIXES = ["/dash", "/review"];

export async function proxy(request: NextRequest) {
  let response = NextResponse.next({ request });

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value }) =>
            request.cookies.set(name, value),
          );
          response = NextResponse.next({ request });
          cookiesToSet.forEach(({ name, value, options }) =>
            response.cookies.set(name, value, options),
          );
        },
      },
    },
  );

  // Refreshes the token as a side effect. Do not remove: without it the session
  // expires mid-visit and server components start seeing a logged-out user.
  const {
    data: { user },
  } = await supabase.auth.getUser();

  const path = request.nextUrl.pathname;
  const isProtected = PROTECTED_PREFIXES.some(
    (prefix) => path === prefix || path.startsWith(`${prefix}/`),
  );

  if (isProtected && !user) {
    const loginUrl = new URL("/login", request.nextUrl);
    loginUrl.searchParams.set("next", path);
    return NextResponse.redirect(loginUrl);
  }

  // Send an already-signed-in visitor on to the dashboard — but not when /login
  // is reporting an error. verifySession() bounces an authenticated user with no
  // `profiles` row (an invitation that half-completed) to /login?error=no-profile;
  // without this guard that user has a session, gets sent back to /dash, fails
  // the same check, and loops forever instead of reading the explanation.
  if (path === "/login" && user && !request.nextUrl.searchParams.has("error")) {
    return NextResponse.redirect(new URL("/dash", request.nextUrl));
  }

  return response;
}

export const config = {
  // Everything except static assets and image optimisation. The session needs
  // refreshing on ordinary page loads, not just protected ones.
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
