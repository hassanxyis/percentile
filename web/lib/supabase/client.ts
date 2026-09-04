import { createBrowserClient } from "@supabase/ssr";

/**
 * Supabase client for the browser.
 *
 * Carries the anon key, which is public by design — it is embedded in the
 * bundle and every student's browser can read it. Row-level security is what
 * protects the data behind it (plan §4, db/migrations/0002_rls.sql).
 *
 * The service-role key must never reach this file or anything that imports it;
 * scripts/check-service-role.sh fails CI if it does.
 */
export function createClient() {
  return createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
  );
}
