"use server";

import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";

import { createClient } from "@/lib/supabase/server";

export type LoginState = { error?: string } | undefined;

/**
 * Email and password sign-in (plan §1, §13).
 *
 * There is no sign-up counterpart, deliberately. Accounts are created by an
 * org_admin through inviteMember in app/dash/settings/actions.ts: `role` decides
 * who can read a psychologist's notes about a minor, and `organisation_id`
 * decides which school's students a person can see. Neither is safe to let a
 * stranger choose for themselves.
 */
export async function login(
  _state: LoginState,
  formData: FormData,
): Promise<LoginState> {
  const email = String(formData.get("email") ?? "").trim();
  const password = String(formData.get("password") ?? "");
  const next = String(formData.get("next") ?? "/dash");

  if (!email || !password) {
    return { error: "Enter your email and password." };
  }

  const supabase = await createClient();
  const { error } = await supabase.auth.signInWithPassword({ email, password });

  if (error) {
    // One message for "no such account" and "wrong password" alike — a
    // distinguishable error tells an attacker which addresses are registered.
    return { error: "Those details did not match an account." };
  }

  revalidatePath("/", "layout");
  // Only relative paths: an attacker-supplied ?next=https://evil.test would
  // otherwise turn the login form into an open redirect.
  redirect(next.startsWith("/") ? next : "/dash");
}
