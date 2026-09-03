/**
 * Marketing placeholder. Real copy lands with M12; this exists so M0's
 * "next build passes" milestone means something, and so the mandatory O*NET
 * attribution in the footer is on screen from day one (plan R6).
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
    </main>
  );
}
