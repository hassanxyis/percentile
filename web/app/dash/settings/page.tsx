import Link from "next/link";

import { requireRole } from "@/lib/dal";

import { getOrganisation } from "../queries";
import { InviteMemberForm, OrganisationForm } from "./settings-forms";

/**
 * /dash/settings — organisation details and member invitations (plan §13).
 *
 * Logo upload and the rest of the branding controls belong to M10; M4 covers
 * the name, the brand colour and the invite flow, since account creation is
 * what unblocks everything else.
 */
export default async function SettingsPage() {
  // Not just for rendering: requireRole redirects a counsellor away before any
  // of this loads. The server actions re-check independently — a page guard
  // does not protect an action reachable by direct POST.
  const session = await requireRole("org_admin", "superadmin");
  const organisation = await getOrganisation();

  return (
    <main className="mx-auto flex w-full max-w-xl flex-col gap-8 px-6 py-16">
      <header className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <Link href="/dash" className="text-sm underline">
          Back to cohorts
        </Link>
      </header>

      <OrganisationForm
        name={organisation?.name ?? ""}
        brandHex={organisation?.brand_hex ?? "#1C6A61"}
        hasLogo={Boolean(organisation?.logo_path)}
      />

      <InviteMemberForm canInviteSuperadmin={session.role === "superadmin"} />
    </main>
  );
}
