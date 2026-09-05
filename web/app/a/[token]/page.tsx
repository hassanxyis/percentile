import { notFound } from "next/navigation";

import { flattenModules, nextUnansweredIndex } from "@/lib/taker";

import { ConsentScreen } from "./consent-screen";
import { loadTaker } from "./queries";
import { Taker } from "./taker";

/**
 * /a/[token] — the student's whole experience (plan.md §13, M6).
 *
 * The route M5's invite links have been pointing at since the roster import
 * shipped. Until this existed, every one of them 404'd.
 *
 * No auth, by design. The token in the URL is the credential; `loadTaker`
 * resolves it by hash and returns null for anything that does not match, which
 * becomes a 404 indistinguishable from a mistyped link.
 *
 * Four states, decided here so the client component only ever renders one:
 *   1. no items loaded   → not ready (R2: this must not crash)
 *   2. not yet consented → the consent screen (§16)
 *   3. submitted         → results are with the counsellor (§9.4)
 *   4. otherwise         → the assessment, resumed at the first unanswered item
 */

export const metadata = {
  title: "Your assessment — Percentile",
  // A shared or forwarded link should not be indexed. The token is in the URL.
  robots: { index: false, follow: false },
};

export default async function TakerPage({ params }: PageProps<"/a/[token]">) {
  const { token } = await params;
  const state = await loadTaker(token);

  if (!state) {
    notFound();
  }

  const { participant, organisation, session, modules, answeredItemIds } = state;
  const firstName = participant.full_name.split(" ")[0];

  // 1. R2: all code must work with an empty item table. A student who arrives
  // before the instruments are loaded gets an explanation, not a stack trace or
  // an assessment with no questions in it.
  if (modules.length === 0) {
    return (
      <Screen>
        <h1 className="text-2xl font-semibold tracking-tight">Not quite ready</h1>
        <p className="text-black/70 dark:text-white/70">
          This assessment has not been set up yet. Please check with{" "}
          {organisation.name} and come back to this link later.
        </p>
      </Screen>
    );
  }

  // 3. Checked before consent: a submitted session has consented by definition,
  // and this is the state a student returning to their link is most likely in.
  if (session?.submitted_at) {
    return (
      <Screen>
        <h1 className="text-2xl font-semibold tracking-tight">
          All done, {firstName}
        </h1>
        <p className="text-black/70 dark:text-white/70">
          Your answers are in. A counsellor at {organisation.name} is going
          through your results now, and you will get your report by email once
          they have.
        </p>
        <p className="text-black/70 dark:text-white/70">
          There is nothing else you need to do. You can close this page.
        </p>
      </Screen>
    );
  }

  // 2. Consent (§16). `consent_at` rather than the session, because consent is
  // the thing being recorded and the session is a consequence of it.
  if (!participant.consent_at) {
    const itemCount = flattenModules(modules).length;
    return (
      <ConsentScreen
        token={token}
        firstName={firstName}
        organisationName={organisation.name}
        itemCount={itemCount}
        moduleCount={modules.length}
      />
    );
  }

  // 4. Resume position comes from the recorded answers, never from
  // `sessions.progress` (R1 — the raw responses are the record).
  const ordered = flattenModules(modules);
  const startIndex = nextUnansweredIndex(ordered, answeredItemIds);

  return (
    <Taker
      token={token}
      modules={modules}
      answeredItemIds={answeredItemIds}
      startIndex={startIndex}
    />
  );
}

function Screen({ children }: { children: React.ReactNode }) {
  return (
    <main className="mx-auto flex w-full max-w-xl flex-col gap-4 px-6 py-16">
      {children}
    </main>
  );
}
