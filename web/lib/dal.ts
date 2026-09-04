import "server-only";

import { cache } from "react";
import { redirect } from "next/navigation";

import { createClient } from "@/lib/supabase/server";

/**
 * Data Access Layer — the one place that answers "who is calling, and what may
 * they see".
 *
 * Auth checks live here rather than in a layout. Next's own guidance is blunt
 * about why: a layout "does not control whether the rest of the route renders"
 * and does not re-render on navigation, so route segments and server actions
 * run regardless of what it decided
 * (node_modules/next/dist/docs/01-app/02-guides/authentication.md).
 *
 * proxy.ts also redirects unauthenticated traffic, but that is a cheap
 * cookie-level check for redirect ergonomics. It is not the boundary. Every
 * page and every server action calls into this file.
 */

export type Role = "counsellor" | "psychologist" | "org_admin" | "superadmin";

export const ROLES: readonly Role[] = [
  "counsellor",
  "psychologist",
  "org_admin",
  "superadmin",
] as const;

export type Session = {
  userId: string;
  organisationId: string;
  fullName: string;
  role: Role;
};

/**
 * The caller's identity, or a redirect to /login.
 *
 * `cache()` memoises per render pass, so a page that checks authorisation and
 * then loads data does not pay for two round trips.
 *
 * Reads `profiles` through the RLS-bound client, and `profiles_self_read` is
 * `id = auth.uid()` — so this returns the caller's own row or nothing at all.
 * There is no way to spoof another user's profile through it.
 */
export const verifySession = cache(async (): Promise<Session> => {
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) {
    redirect("/login");
  }

  const { data: profile } = await supabase
    .from("profiles")
    .select("id, organisation_id, full_name, role")
    .eq("id", user.id)
    .single();

  // An auth.users row with no profile is an incomplete invite: the account
  // exists but was never attached to an organisation. It has no tenant, so it
  // has no data — send it back to /login rather than rendering an empty
  // dashboard that looks like a bug.
  if (!profile) {
    redirect("/login?error=no-profile");
  }

  return {
    userId: profile.id,
    organisationId: profile.organisation_id,
    fullName: profile.full_name,
    role: profile.role as Role,
  };
});

/**
 * The caller's identity, or a redirect, when they hold one of `allowed`.
 *
 * Call this first in every server action. A server action is reachable by
 * direct POST, not only through your own UI, so hiding a button is not
 * authorisation.
 */
export const requireRole = cache(
  async (...allowed: Role[]): Promise<Session> => {
    const session = await verifySession();

    if (!allowed.includes(session.role)) {
      redirect("/dash?error=forbidden");
    }

    return session;
  },
);

/**
 * Whether a role may work the psychologist review queue (§9.1).
 *
 * Mirrors the `role in ('psychologist', 'org_admin', 'superadmin')` test in
 * 0003_reviews.sql's policies. The database is the enforcement point; this is
 * for deciding what to render.
 */
export function canReviewSessions(role: Role): boolean {
  return role === "psychologist" || role === "org_admin" || role === "superadmin";
}
