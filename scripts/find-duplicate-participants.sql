-- Find (and then remove) duplicate participants within a cohort.
--
-- NOT a migration. This is a one-off repair script you run by hand against a
-- specific database. It lives in scripts/ precisely so it is never picked up by
-- the harness glob in engine/tests/db/harness.py.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- WHY THIS EXISTS
--
-- Re-importing the same roster CSV inserts a second full set of participants.
-- `parseRosterCsv` dedupes emails *within* one file, but `participants` is
-- unique only on `invite_token_hash`, and every import mints fresh random
-- tokens — so a second upload of the same file collides with nothing.
--
-- `0007_participant_email_unique.sql` adds the constraint that prevents it.
-- That index CANNOT be created while duplicates exist, so this script runs
-- first.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- READ BEFORE RUNNING STEP 2
--
-- Deleting a participant is IRREVERSIBLE and CASCADES:
--
--     participants → sessions → responses
--                            → scores
--                            → reviews → career_directions
--                            → reports
--
-- A student who has already answered questions loses those answers. Raw
-- responses are the one thing the whole system treats as unrecoverable (R1):
-- they cannot be reconstructed from a score, and the student would have to sit
-- the assessment again.
--
-- Step 1 is a read. Run it, look at what comes back, and only then decide.
-- Step 2 is deliberately not wrapped in a transaction you can forget to commit
-- — it is a single statement, and you should run it on its own.
-- ─────────────────────────────────────────────────────────────────────────────


-- ── STEP 1 — LOOK FIRST. This only reads. ───────────────────────────────────
--
-- One row per duplicated email, showing how many copies exist and what state
-- each is in. Check the `statuses` column before deleting anything: if a group
-- shows more than one row that is not 'invited', two different copies have real
-- work attached and the automatic rule in step 2 is the wrong tool. Sort that
-- group out by hand.

select
  c.name                                             as cohort,
  p.cohort_id,
  lower(p.email)                                     as email,
  count(*)                                           as copies,
  array_agg(p.full_name order by p.created_at)       as names,
  array_agg(p.status    order by p.created_at)       as statuses,
  array_agg(p.created_at order by p.created_at)      as created
  -- Which row survives is decided in step 2a, by answer count rather than by
  -- age. Do not infer it from the order here.
from participants p
join cohorts c on c.id = p.cohort_id
where p.email is not null
group by c.name, p.cohort_id, lower(p.email)
having count(*) > 1
order by c.name, lower(p.email);


-- ── STEP 1b — how much real work is attached to each duplicate? ─────────────
--
-- Informational. Step 2 keeps whichever copy has the most answers, so a single
-- row here is the normal case and not a problem: it is the copy that will
-- SURVIVE, and its empty twin is the one that goes.
--
-- What to look for is TWO rows for the same student, both with answers. No
-- automatic rule can choose between those — step 2a flags it with STOP and you
-- resolve it by hand.

with ranked as (
  select
    p.id,
    p.full_name,
    p.status,
    row_number() over (
      partition by p.cohort_id, lower(p.email)
      order by p.created_at
    ) as copy_number
  from participants p
  where p.email is not null
)
select
  r.id,
  r.full_name,
  r.status,
  count(distinct s.id)  as sessions,
  count(distinct resp.id) as responses,
  count(distinct rev.id)  as reviews
from ranked r
join sessions s        on s.participant_id = r.id
left join responses resp on resp.session_id = s.id
left join reviews rev    on rev.session_id  = s.id
where r.copy_number > 1          -- only the copies step 2 would delete
group by r.id, r.full_name, r.status
order by responses desc;


-- ── STEP 2 — THE DELETE. Run only after reading step 1 and 1b. ──────────────
--
-- Keeps the row with the MOST ANSWERS per (cohort, lower(email)), falling back
-- to the earliest when they are level. Deletes the rest.
--
-- The ordering is deliberate and was corrected after a real near-miss: the
-- first version of this script kept the earliest row, and on the M6 test cohort
-- the student's 110 answers were on the *later* copy — the earlier one was an
-- empty `invited` shell created by the first import. Keeping "the oldest"
-- would have destroyed the only real assessment in the database.
--
-- Ranking by answer count makes the common case right automatically: an empty
-- duplicate always loses to a copy someone actually used.
--
-- STILL CHECK STEP 2a FIRST. If two copies of the same student both hold
-- answers, no automatic rule is safe — sort that group out by hand.
--
-- This deletes participant rows. It cascades to sessions, responses, scores,
-- reviews and reports, and it cannot be undone.

-- ── STEP 2a — what would this actually keep and delete? Read-only. ──────────
--
-- Run this before step 2b and read it. Every `delete` line should be a row you
-- are content to lose; every `KEEP` line should be the copy with the answers.

with answer_counts as (
  select
    p.id,
    p.cohort_id,
    p.full_name,
    p.email,
    p.status,
    p.created_at,
    (
      select count(*)
      from responses r
      join sessions s on s.id = r.session_id
      where s.participant_id = p.id
    ) as answers
  from participants p
  where p.email is not null
),
ranked as (
  select
    *,
    row_number() over (
      partition by cohort_id, lower(email)
      order by answers desc, created_at
    ) as copy_number,
    count(*) filter (where answers > 0) over (
      partition by cohort_id, lower(email)
    ) as copies_with_answers
  from answer_counts
)
select
  case when copy_number = 1 then 'KEEP' else 'delete' end as action,
  full_name,
  email,
  status,
  answers,
  created_at,
  case
    when copies_with_answers > 1
      then 'STOP — more than one copy has answers; resolve by hand'
    else ''
  end as warning
from ranked
where (cohort_id, lower(email)) in (
  select cohort_id, lower(email)
  from participants
  where email is not null
  group by cohort_id, lower(email)
  having count(*) > 1
)
order by email, copy_number;


-- ── STEP 2b — THE DELETE ITSELF. Uncomment only after reading 2a. ───────────
--
-- If any row in 2a carried the STOP warning, do not run this.

-- delete from participants p
-- using (
--   select
--     id,
--     row_number() over (
--       partition by cohort_id, lower(email)
--       order by (
--         select count(*)
--         from responses r
--         join sessions s on s.id = r.session_id
--         where s.participant_id = participants.id
--       ) desc,
--       created_at
--     ) as copy_number
--   from participants
--   where email is not null
-- ) dupe
-- where p.id = dupe.id
--   and dupe.copy_number > 1;


-- ── STEP 3 — confirm before adding the index. ───────────────────────────────
--
-- Must return zero rows. If it does, 0007_participant_email_unique.sql will
-- apply cleanly.

select p.cohort_id, lower(p.email) as email, count(*)
from participants p
where p.email is not null
group by p.cohort_id, lower(p.email)
having count(*) > 1;
