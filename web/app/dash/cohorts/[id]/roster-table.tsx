"use client";

import { useActionState, useState, useTransition } from "react";

import { describeIntendedField } from "@/lib/intended-fields";
import {
  canResendInvite,
  isOverdue,
  statusLabel,
  waitingFor,
} from "@/lib/roster";

import { reportDownloadUrl, resendInvite } from "./actions";
import type { ParticipantRow } from "./queries";

/**
 * The roster table (plan §13, M10).
 *
 * A client component because two controls need per-row state — which student is
 * being resent, and which download is in flight. The reads all happen on the
 * server; this receives rows and renders them.
 *
 * `now` is passed in from the server rather than computed here. Rendering
 * `new Date()` during hydration produces a different value than the server's
 * and React logs a mismatch — and "3 days" flickering to "4 days" on hydration
 * is exactly the kind of thing that makes a counsellor distrust the screen.
 */
export function RosterTable({
  participants,
  now,
}: {
  participants: ParticipantRow[];
  now: string;
}) {
  const [state, formAction, pending] = useActionState(resendInvite, undefined);
  const renderedAt = new Date(now);

  return (
    <div className="flex flex-col gap-4">
      {state?.error && (
        <p className="text-sm text-red-700 dark:text-red-400">{state.error}</p>
      )}
      {state?.ok && (
        <p className="text-sm text-green-800 dark:text-green-400">{state.ok}</p>
      )}

      <table className="text-sm">
        <thead>
          <tr className="text-left text-xs uppercase text-black/50 dark:text-white/50">
            <th className="py-2 pr-4 font-medium">Name</th>
            <th className="py-2 pr-4 font-medium">Roll no.</th>
            <th className="py-2 pr-4 font-medium">Intended field</th>
            <th className="py-2 pr-4 font-medium">Status</th>
            <th className="py-2 font-medium">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {participants.map((participant) => {
            const overdue = isOverdue(
              participant.status,
              participant.submitted_at,
              renderedAt,
            );

            return (
              <tr
                key={participant.id}
                className="border-t border-black/10 dark:border-white/10"
              >
                <td className="py-2 pr-4">{participant.full_name}</td>
                <td className="py-2 pr-4 text-black/60 dark:text-white/60">
                  {participant.external_ref ?? "—"}
                </td>
                <td className="py-2 pr-4 text-black/60 dark:text-white/60">
                  {describeIntendedField(participant.intended_field)}
                </td>
                <td className="py-2 pr-4">
                  {statusLabel(participant.status)}
                  {/* §9.4's five days, from the counsellor's side. The person who
                      can chase a psychologist is reading this screen, not the
                      alert mailbox. */}
                  {overdue && (
                    <span className="ml-2 text-xs text-amber-700 dark:text-amber-500">
                      waiting {waitingFor(participant.submitted_at, renderedAt)}
                    </span>
                  )}
                </td>
                <td className="py-2">
                  <div className="flex items-center justify-end gap-3">
                    {participant.report_id && (
                      <DownloadReport
                        reportId={participant.report_id}
                        studentName={participant.full_name}
                      />
                    )}

                    {canResendInvite(participant.status) && (
                      <form action={formAction}>
                        <input
                          type="hidden"
                          name="participant_id"
                          value={participant.id}
                        />
                        <button
                          type="submit"
                          disabled={pending}
                          className="text-xs underline disabled:opacity-50"
                        >
                          Resend invite
                        </button>
                      </form>
                    )}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/**
 * One report download.
 *
 * The signed URL is fetched on click, never rendered into the page. A URL in
 * the HTML would be in view-source, in any screenshot of this screen, and — on
 * the shared school computers §16 assumes — readable by whoever sits down next.
 *
 * `window.open` with the URL rather than a redirect, so the roster stays put.
 */
function DownloadReport({
  reportId,
  studentName,
}: {
  reportId: string;
  studentName: string;
}) {
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  return (
    <span className="flex items-center gap-2">
      <button
        type="button"
        disabled={pending}
        className="text-xs underline disabled:opacity-50"
        onClick={() => {
          setError(null);
          startTransition(async () => {
            const result = await reportDownloadUrl(reportId);
            if (result.error || !result.url) {
              setError(result.error ?? "Could not open that report.");
              return;
            }
            window.open(result.url, "_blank", "noopener,noreferrer");
          });
        }}
      >
        {pending ? "Opening…" : "Report"}
        <span className="sr-only"> for {studentName}</span>
      </button>
      {error && <span className="text-xs text-red-700 dark:text-red-400">{error}</span>}
    </span>
  );
}
