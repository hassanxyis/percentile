"use client";

import { useActionState } from "react";

import { updateOrganisation, type ActionState } from "../actions";
import { inviteMember } from "./actions";

const FIELD =
  "rounded border border-black/20 px-3 py-2 dark:border-white/20";
const BUTTON =
  "self-start rounded bg-black px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-black";

function Feedback({ state }: { state: ActionState }) {
  if (state?.error) {
    return (
      <p role="alert" className="text-sm text-red-600 dark:text-red-400">
        {state.error}
      </p>
    );
  }
  if (state?.ok) {
    return <p className="text-sm text-green-700 dark:text-green-400">{state.ok}</p>;
  }
  return null;
}

export function OrganisationForm({
  name,
  brandHex,
  hasLogo,
}: {
  name: string;
  brandHex: string;
  hasLogo: boolean;
}) {
  const [state, formAction, pending] = useActionState<ActionState, FormData>(
    updateOrganisation,
    undefined,
  );

  return (
    <form action={formAction} className="flex flex-col gap-3">
      <h2 className="text-sm font-medium">Institution</h2>
      <p className="text-xs text-black/60 dark:text-white/60">
        The name, colour and logo appear on every student report.
      </p>

      <label className="flex flex-col gap-1 text-sm">
        <span>Name</span>
        <input name="name" defaultValue={name} required className={FIELD} />
      </label>

      <label className="flex flex-col gap-1 text-sm">
        <span>Brand colour</span>
        <input
          name="brand_hex"
          defaultValue={brandHex}
          placeholder="#1C6A61"
          className={FIELD}
        />
      </label>

      <label className="flex flex-col gap-1 text-sm">
        <span>Logo</span>
        {/* Not `required`: saving the name alone must not force a re-upload.
            An empty file input arrives with size 0 and the action treats that
            as "unchanged" rather than as a request to clear the logo. */}
        <input
          type="file"
          name="logo"
          accept="image/png,image/jpeg,image/webp,image/svg+xml"
          className={FIELD}
        />
        <span className="text-xs text-black/60 dark:text-white/60">
          {hasLogo
            ? "A logo is set. Choosing a file replaces it."
            : "PNG, JPEG, WebP or SVG, up to 2 MB."}
        </span>
      </label>

      <Feedback state={state} />
      <button type="submit" disabled={pending} className={BUTTON}>
        {pending ? "Saving…" : "Save"}
      </button>
    </form>
  );
}

export function InviteMemberForm({ canInviteSuperadmin }: { canInviteSuperadmin: boolean }) {
  const [state, formAction, pending] = useActionState<ActionState, FormData>(
    inviteMember,
    undefined,
  );

  return (
    <form
      action={formAction}
      className="flex flex-col gap-3 border-t border-black/10 pt-6 dark:border-white/10"
    >
      <h2 className="text-sm font-medium">Invite a colleague</h2>
      <p className="text-xs text-black/60 dark:text-white/60">
        They receive an email to set their own password. Reviewers need the
        psychologist role to open the review queue.
      </p>

      <label className="flex flex-col gap-1 text-sm">
        <span>Full name</span>
        <input name="full_name" required className={FIELD} />
      </label>

      <label className="flex flex-col gap-1 text-sm">
        <span>Email</span>
        <input type="email" name="email" required className={FIELD} />
      </label>

      <label className="flex flex-col gap-1 text-sm">
        <span>Role</span>
        <select name="role" defaultValue="counsellor" className={FIELD}>
          <option value="counsellor">Counsellor</option>
          <option value="psychologist">Psychologist</option>
          <option value="org_admin">Administrator</option>
          {canInviteSuperadmin && <option value="superadmin">Superadmin</option>}
        </select>
      </label>

      <Feedback state={state} />
      <button type="submit" disabled={pending} className={BUTTON}>
        {pending ? "Sending…" : "Send invitation"}
      </button>
    </form>
  );
}
