import Link from "next/link";
import { notFound } from "next/navigation";

import { requireRole } from "@/lib/dal";

import { getCohort } from "../queries";
import { UploadForm } from "./upload-form";

/**
 * /dash/cohorts/[id]/upload — roster CSV import (plan.md §13, M5).
 */
export default async function UploadPage({
  params,
}: PageProps<"/dash/cohorts/[id]/upload">) {
  const { id } = await params;

  // Not only for rendering: this redirects a psychologist away before the form
  // loads. The action re-checks independently, since a server action is
  // reachable by direct POST regardless of what this page decided.
  await requireRole("counsellor", "org_admin", "superadmin");

  const cohort = await getCohort(id);
  if (!cohort) {
    notFound();
  }

  return (
    <main className="mx-auto flex w-full max-w-2xl flex-col gap-8 px-6 py-16">
      <header className="flex items-baseline justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Upload roster</h1>
          <p className="text-sm text-black/60 dark:text-white/60">{cohort.name}</p>
        </div>
        <Link href={`/dash/cohorts/${id}`} className="text-sm underline">
          Back to the roster
        </Link>
      </header>

      <UploadForm cohortId={id} cohortName={cohort.name} />
    </main>
  );
}
