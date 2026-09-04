import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";

/**
 * Supabase client for server components, server actions and route handlers.
 *
 * Uses the anon key and the caller's session cookie, so **row-level security
 * applies**. Every read the counsellor dashboard performs goes through this
 * client, which means the dashboard exercises exactly the policies that
 * engine/tests/db/ asserts — one enforcement path, not two.
 *
 * Reach for lib/supabase/admin.ts only when a write genuinely needs to bypass
 * RLS, and never to make a read "just work".
 */
export async function createClient() {
  const cookieStore = await cookies();

  return createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return cookieStore.getAll();
        },
        setAll(cookiesToSet) {
          try {
            cookiesToSet.forEach(({ name, value, options }) =>
              cookieStore.set(name, value, options),
            );
          } catch {
            // Server components cannot set headers. Harmless here: proxy.ts
            // refreshes the session on every request, so the write this call
            // would have made has already happened upstream.
          }
        },
      },
    },
  );
}
