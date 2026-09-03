/**
 * Site footer. The O*NET attribution is mandatory on every page and every
 * report — it is a licence term, not a courtesy (plan R6, §6).
 */
export function Footer() {
  return (
    <footer className="border-t border-black/10 dark:border-white/10 px-6 py-8 text-xs leading-relaxed text-black/60 dark:text-white/60">
      <div className="mx-auto max-w-3xl space-y-2">
        <p>
          This site incorporates information from the O*NET Database by the
          U.S. Department of Labor, Employment and Training Administration
          (USDOL/ETA). O*NET® is a trademark of USDOL/ETA.
        </p>
        <p>
          Percentile measures career interests, personality and work values. It
          does not diagnose, screen or assess mental health, ability or
          intelligence.
        </p>
      </div>
    </footer>
  );
}
