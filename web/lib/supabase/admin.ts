import "server-only";

import { createClient as createSupabaseClient } from "@supabase/supabase-js";

/**
 * Supabase client holding the service-role key.
 *
 * **This bypasses row-level security entirely.** Every tenant's rows are
 * visible and writable through it. That is correct for server-side writes —
 * 0002_rls.sql grants authenticated users SELECT only, so all inserts and
 * updates happen here — and it is a full database breach anywhere a browser can
 * reach.
 *
 * Two guards, and they are not redundant:
 *
 *   1. `import "server-only"` above turns any client-component import into a
 *      build error.
 *   2. scripts/check-service-role.sh fails CI if the key reaches a client
 *      component or a browser-visible environment variable.
 *
 * Callers must scope every query themselves. The organisation_id comes from
 * verifySession() in lib/dal.ts — never from a form field, since a server
 * action is reachable by direct POST.
 */
export function createAdminClient() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const serviceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY;

  if (!url || !serviceRoleKey) {
    throw new Error(
      "NEXT_PUBLIC_SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in web/.env.local",
    );
  }

  return createSupabaseClient(url, serviceRoleKey, {
    auth: { autoRefreshToken: false, persistSession: false },
  });
}
