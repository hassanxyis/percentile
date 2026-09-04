-- Percentile v2 — close the UPDATE path around R9 (plan.md R9, §5, Appendix B).
--
-- Never edit a migration that has run. Add a new one.
--
-- 0003_reviews.sql created the trigger as `before insert on reports`. That
-- enforces R9 at the moment a row is created and nowhere else, so this walks
-- straight past it:
--
--     update reports
--        set kind = 'student', session_id = '<a session with no review>'
--      where id = '<some existing cohort report>';
--
-- Appendix B states the rule as an invariant over rows, not over inserts:
-- "No `reports` row for a student ever exists without a confirmed `reviews`
-- row (R9, enforced by the database trigger, not just application code)."
-- An insert-only trigger does not deliver that sentence. CLAUDE.md's standing
-- instruction is to treat any code path that bypasses this gate as a
-- severity-1 bug; an UPDATE is such a path.
--
-- The trigger function itself is unchanged and still correct for both events:
-- it reads NEW.kind and NEW.session_id, which UPDATE populates the same way
-- INSERT does.

drop trigger if exists trg_enforce_review_before_student_report on reports;

create trigger trg_enforce_review_before_student_report
  before insert or update on reports
  for each row execute function enforce_review_before_student_report();

-- Note on scope: this fires on every UPDATE of a student report, including
-- ones that do not touch `kind` or `session_id` (e.g. re-rendering bumps
-- `storage_path`). That is intentional. The check is a cheap EXISTS, and a
-- student report whose review was later reopened to `needs_more_info` *should*
-- fail to update until it is confirmed again — that is the invariant, not a
-- side effect of it.
