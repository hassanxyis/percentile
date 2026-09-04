import Link from "next/link";

import { canReviewSessions, verifySession } from "@/lib/dal";

import { CreateCohortForm } from "./create-cohort-form";
import { listCohorts } from "./queries";

/**
 * /dash — the counsellor's cohort list (plan §13).
 *
 * Deliberately plain. M10 builds the real dashboard with roster progress,
 * invite resends and branding; M4 needs somewhere the CRUD actually lands so
 * the auth and RLS wiring can be exercised end to end.
 */
export default async function DashboardPage({ searchParams }: PageProps<"/dash">) {
  const session = await verifySession();
  const cohorts = await listCohorts();
  const params = await searchParams;

  return (
    <main className="mx-auto flex w-full max-w-3xl flex-col gap-8 px-6 py-16">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Cohorts</h1>
          <p className="text-sm text-black/60 dark:text-white/60">
            {session.fullName} · {session.role.replace("_", " ")}
          </p>
        </div>

        <div className="flex items-center gap-4 text-sm">
          {canReviewSessions(session.role) && (
            <Link href="/review" className="underline">
              Review queue
            </Link>
          )}
          <Link href="/dash/settings" className="underline">
            Settings
          </Link>
          <form action="/logout" method="post">
            <button type="submit" className="underline">
              Sign out
            </button>
          </form>
        </div>
      </header>

      {params.error === "forbidden" && (
        <p
          role="alert"
          className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm"
        >
          That area is not open to your role.
        </p>
      )}

      {cohorts.length === 0 ? (
        <p className="text-sm text-black/60 dark:text-white/60">
          No cohorts yet.
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {cohorts.map((cohort) => (
            <li key={cohort.id}>
              <Link
                href={`/dash/cohorts/${cohort.id}`}
                className="flex items-baseline justify-between rounded border border-black/10 px-4 py-3 hover:bg-black/5 dark:border-white/10 dark:hover:bg-white/5"
              >
                <span className="font-medium">{cohort.name}</span>
                <span className="text-xs text-black/50 dark:text-white/50">
                  {[cohort.education_level, cohort.intake_year]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}

      <CreateCohortForm />
    </main>
  );
}
