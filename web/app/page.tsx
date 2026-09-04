import Link from "next/link";

/**
 * Marketing placeholder. Real copy lands with M12; this exists so M0's
 * "next build passes" milestone means something, and so the mandatory O*NET
 * attribution in the footer is on screen from day one (plan R6).
 *
 * The sign-in link is not decoration: without it nothing on the site reaches
 * /login, and the counsellor dashboard is undiscoverable except by typing the
 * URL. Students never sign in — they arrive on a tokenised link (§5, §13).
 */
export default function Home() {
  return (
    <main className="mx-auto flex max-w-3xl flex-col gap-6 px-6 py-24">
      <h1 className="text-4xl font-semibold tracking-tight">Percentile</h1>
      <p className="text-lg text-black/70 dark:text-white/70">
        Career interest, personality and work-values assessment for schools and
        colleges. A counsellor uploads a roster; students complete a 25-minute
        assessment on their phones; the institution gets a cohort report it can
        act on.
      </p>
      <p className="text-sm text-black/50 dark:text-white/50">In development.</p>
      <Link
        href="/login"
        className="self-start rounded bg-black px-4 py-2 text-sm font-medium text-white dark:bg-white dark:text-black"
      >
        Counsellor sign in
      </Link>
    </main>
  );
}
