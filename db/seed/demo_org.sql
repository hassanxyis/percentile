-- Demo data for the database test harness (plan.md §3).
--
-- Two organisations, because a cross-tenant leak test needs something to leak
-- *from*. Every table that the leak test counts rows in is populated for BOTH
-- orgs — otherwise "org B sees zero rows" passes because the table is empty
-- rather than because RLS filtered it, and the test proves nothing.
--
-- All ids are fixed literals so tests can name them as constants instead of
-- querying for them (see engine/tests/db/harness.py). Not gen_random_uuid().
--
-- Applied by the harness after every migration. Also usable by hand against a
-- scratch Supabase project: `psql "$URL" -f db/seed/demo_org.sql`. Never
-- against production — the ids are guessable by design.
--
-- Item text is placeholder. Real instrument text is loaded from
-- data/instruments/*.csv by scripts/load_instruments.py and is never invented
-- (R2); these rows exist only because responses.item_id demands a referent.

-- ── auth.users ───────────────────────────────────────────────────────────
-- profiles.id references this. In production GoTrue writes these rows; here
-- the shim's table stands in for it.
insert into auth.users (id, email) values
  ('aaaaaaa1-0000-4000-8000-000000000001', 'counsellor@demo-a.test'),
  ('aaaaaaa2-0000-4000-8000-000000000002', 'psychologist@demo-a.test'),
  ('aaaaaaa3-0000-4000-8000-000000000003', 'admin@demo-a.test'),
  ('bbbbbbb1-0000-4000-8000-000000000001', 'counsellor@demo-b.test'),
  ('bbbbbbb2-0000-4000-8000-000000000002', 'psychologist@demo-b.test');

-- ── organisations ────────────────────────────────────────────────────────
insert into organisations (id, name, slug, brand_hex, plan) values
  ('11111111-1111-4111-8111-111111111111', 'Demo Academy',  'demo-a', '#1C6A61', 'pilot'),
  ('22222222-2222-4222-8222-222222222222', 'Rival College', 'demo-b', '#7A3E9D', 'pilot');

-- ── profiles ─────────────────────────────────────────────────────────────
-- The counsellor/psychologist pair inside org A is what test_review_rls_boundary
-- exercises. The org B pair proves the boundary is organisation-scoped as well
-- as role-scoped: a psychologist at Rival College must not read Demo Academy's
-- interview notes either.
insert into profiles (id, organisation_id, full_name, role) values
  ('aaaaaaa1-0000-4000-8000-000000000001', '11111111-1111-4111-8111-111111111111',
   'Ayesha Counsellor',   'counsellor'),
  ('aaaaaaa2-0000-4000-8000-000000000002', '11111111-1111-4111-8111-111111111111',
   'Dr Bilal Reviewer',   'psychologist'),
  ('aaaaaaa3-0000-4000-8000-000000000003', '11111111-1111-4111-8111-111111111111',
   'Cara Administrator',  'org_admin'),
  ('bbbbbbb1-0000-4000-8000-000000000001', '22222222-2222-4222-8222-222222222222',
   'Danish Counsellor',   'counsellor'),
  ('bbbbbbb2-0000-4000-8000-000000000002', '22222222-2222-4222-8222-222222222222',
   'Dr Erum Reviewer',    'psychologist');

-- ── reference data (shared, not tenant-scoped) ───────────────────────────
insert into instruments (code, title, version, source, licence, item_count) values
  ('interests',   'O*NET Interest Profiler Short Form', 'v1',
   'onetcenter.org', 'Public domain, USDOL/ETA. Attribution required (R6).', 2),
  ('personality', 'IPIP Big-Five Factor Markers (50-item)', 'IPIP-50',
   'ipip.ori.org',   'Public domain — International Personality Item Pool.',  2);

insert into items (id, instrument_code, ordinal, code, text, scale,
                   reverse_keyed, response_min, response_max) values
  ('11eeee00-0000-4000-8000-000000000001', 'interests',   1, 'SEED_IP_R_01',
   'placeholder interest item 1',    'R', false, 0, 1),
  ('11eeee00-0000-4000-8000-000000000002', 'interests',   2, 'SEED_IP_I_01',
   'placeholder interest item 2',    'I', false, 0, 1),
  ('11eeee00-0000-4000-8000-000000000003', 'personality', 1, 'SEED_IPIP_O_01',
   'placeholder personality item 1', 'O', false, 1, 5),
  ('11eeee00-0000-4000-8000-000000000004', 'personality', 2, 'SEED_IPIP_C_01',
   'placeholder personality item 2', 'C', true,  1, 5);

-- career_directions.onet_soc_code references this.
insert into occupations (onet_soc_code, title, job_zone,
                         interest_r, interest_i, interest_a,
                         interest_s, interest_e, interest_c,
                         pk_relevant, entrepreneurial_track) values
  ('15-1252.00', 'Software Developers',   4, 2, 7, 3, 2, 3, 5, true,  false),
  ('29-1141.00', 'Registered Nurses',     4, 3, 5, 2, 7, 3, 4, true,  false),
  ('11-1021.00', 'General Managers',      4, 2, 3, 2, 4, 7, 5, true,  true);

-- ── org A: cohort → participants → sessions → responses → scores ─────────
insert into cohorts (id, organisation_id, name, intake_year, education_level, created_by) values
  ('c0000001-0000-4000-8000-00000000000a', '11111111-1111-4111-8111-111111111111',
   'Class of 2027 — Pre-Medical A', 2027, 'intermediate',
   'aaaaaaa1-0000-4000-8000-000000000001'),
  ('c0000002-0000-4000-8000-00000000000b', '22222222-2222-4222-8222-222222222222',
   'Class of 2027 — Commerce B',    2027, 'intermediate',
   'bbbbbbb1-0000-4000-8000-000000000001');

-- Two participants per cohort: one that reaches a confirmed review and a
-- report, one left at `scored` so there is a row the R9 gate would still block.
insert into participants (id, cohort_id, full_name, email, external_ref,
                          intended_field, education_level, invite_token_hash,
                          consent_at, status) values
  ('a0aaaaa1-0000-4000-8000-000000000001', 'c0000001-0000-4000-8000-00000000000a',
   'Fatima Student', 'fatima@demo-a.test', 'A-001', 'medicine', 'intermediate',
   'seedhash-a-0000000000000000000000000000000000000000000000000000000001',
   now(), 'confirmed'),
  ('a0aaaaa2-0000-4000-8000-000000000002', 'c0000001-0000-4000-8000-00000000000a',
   'Hamza Student',  'hamza@demo-a.test',  'A-002', 'engineering', 'intermediate',
   'seedhash-a-0000000000000000000000000000000000000000000000000000000002',
   now(), 'scored'),
  ('b0bbbbb1-0000-4000-8000-000000000001', 'c0000002-0000-4000-8000-00000000000b',
   'Iqra Student',   'iqra@demo-b.test',   'B-001', 'commerce', 'intermediate',
   'seedhash-b-0000000000000000000000000000000000000000000000000000000001',
   now(), 'confirmed'),
  ('b0bbbbb2-0000-4000-8000-000000000002', 'c0000002-0000-4000-8000-00000000000b',
   'Junaid Student', 'junaid@demo-b.test', 'B-002', 'commerce', 'intermediate',
   'seedhash-b-0000000000000000000000000000000000000000000000000000000002',
   now(), 'scored');

insert into sessions (id, participant_id, started_at, submitted_at, last_seen_at, progress) values
  ('5e551011-0000-4000-8000-00000000a001', 'a0aaaaa1-0000-4000-8000-000000000001',
   now(), now(), now(), '{"interests": 60, "personality": 50}'::jsonb),
  ('5e551011-0000-4000-8000-00000000a002', 'a0aaaaa2-0000-4000-8000-000000000002',
   now(), now(), now(), '{"interests": 60, "personality": 50}'::jsonb),
  ('5e551011-0000-4000-8000-00000000b001', 'b0bbbbb1-0000-4000-8000-000000000001',
   now(), now(), now(), '{"interests": 60, "personality": 50}'::jsonb),
  ('5e551011-0000-4000-8000-00000000b002', 'b0bbbbb2-0000-4000-8000-000000000002',
   now(), now(), now(), '{"interests": 60, "personality": 50}'::jsonb);

insert into responses (session_id, item_id, value, ms_elapsed) values
  ('5e551011-0000-4000-8000-00000000a001', '11eeee00-0000-4000-8000-000000000001', 1, 1400),
  ('5e551011-0000-4000-8000-00000000a001', '11eeee00-0000-4000-8000-000000000003', 4, 2100),
  ('5e551011-0000-4000-8000-00000000a002', '11eeee00-0000-4000-8000-000000000001', 0, 1200),
  ('5e551011-0000-4000-8000-00000000b001', '11eeee00-0000-4000-8000-000000000001', 1, 1600),
  ('5e551011-0000-4000-8000-00000000b001', '11eeee00-0000-4000-8000-000000000003', 2, 1900),
  ('5e551011-0000-4000-8000-00000000b002', '11eeee00-0000-4000-8000-000000000001', 0, 1100);

-- values_scores carries the GET2 shape in v2 (0003_reviews.sql's comment on the
-- column). The column was not renamed, so that rescoreable history stays intact.
insert into scores (session_id, engine_version, interests, personality, values_scores, flags) values
  ('5e551011-0000-4000-8000-00000000a001', '1.0.0',
   '{"R":12,"I":38,"A":9,"S":31,"E":14,"C":22,"code":"ISC","differentiation":29,"band":"moderate"}'::jsonb,
   '{"O":41,"C":33,"E":22,"A":38,"S":27}'::jsonb,
   '{"instrument":null,"status":"module_not_administered"}'::jsonb,
   '[]'::jsonb),
  ('5e551011-0000-4000-8000-00000000a002', '1.0.0',
   '{"R":34,"I":29,"A":8,"S":11,"E":13,"C":19,"code":"RIC","differentiation":26,"band":"moderate"}'::jsonb,
   '{"O":30,"C":40,"E":19,"A":25,"S":31}'::jsonb,
   '{"instrument":null,"status":"module_not_administered"}'::jsonb,
   '[]'::jsonb),
  ('5e551011-0000-4000-8000-00000000b001', '1.0.0',
   '{"R":10,"I":18,"A":12,"S":24,"E":36,"C":30,"code":"ECS","differentiation":26,"band":"moderate"}'::jsonb,
   '{"O":28,"C":36,"E":34,"A":30,"S":22}'::jsonb,
   '{"instrument":null,"status":"module_not_administered"}'::jsonb,
   '[]'::jsonb),
  ('5e551011-0000-4000-8000-00000000b002', '1.0.0',
   '{"R":15,"I":16,"A":14,"S":20,"E":33,"C":28,"code":"ECS","differentiation":19,"band":"low"}'::jsonb,
   '{"O":26,"C":31,"E":29,"A":27,"S":24}'::jsonb,
   '{"instrument":null,"status":"module_not_administered"}'::jsonb,
   '["undifferentiated"]'::jsonb);

insert into occupation_matches (session_id, engine_version, rank, onet_soc_code, score,
                                local_title, local_pathway) values
  ('5e551011-0000-4000-8000-00000000a001', '1.0.0', 1, '29-1141.00', 0.8412,
   'Registered Nurse', 'FSc Pre-Medical → BSc Nursing'),
  ('5e551011-0000-4000-8000-00000000a001', '1.0.0', 2, '15-1252.00', 0.7733,
   'Software Developer', 'FSc Pre-Engineering → BS Computer Science'),
  ('5e551011-0000-4000-8000-00000000b001', '1.0.0', 1, '11-1021.00', 0.8105,
   'General Manager', 'ICom → BBA');

-- ── reviews (org A and org B) ────────────────────────────────────────────
-- interview_notes must be non-NULL in both orgs: it is the exact column
-- test_review_rls_boundary tries to reach across the counsellor/psychologist
-- line. A NULL here would make "the counsellor read nothing" ambiguous.
insert into reviews (id, session_id, reviewer_id, status, interview_mode,
                     interview_at, interview_notes, flags_reviewed, confirmed_at) values
  ('4e415e00-0000-4000-8000-00000000a001', '5e551011-0000-4000-8000-00000000a001',
   'aaaaaaa2-0000-4000-8000-000000000002', 'confirmed', 'in_person', now(),
   'Spoke with Fatima about the gap between her stated interest in medicine and a '
   'strong Investigative-Conventional profile. Confident and articulate; the '
   'family expectation is real but she is not being pushed.',
   '[]'::jsonb, now()),
  ('4e415e00-0000-4000-8000-00000000b001', '5e551011-0000-4000-8000-00000000b001',
   'bbbbbbb2-0000-4000-8000-000000000002', 'confirmed', 'video', now(),
   'Iqra is clear about commerce and the Enterprising profile supports it. '
   'Short conversation, nothing flagged.',
   '[]'::jsonb, now());

insert into review_events (review_id, actor, event, meta) values
  ('4e415e00-0000-4000-8000-00000000a001', 'aaaaaaa2-0000-4000-8000-000000000002',
   'opened',    '{}'::jsonb),
  ('4e415e00-0000-4000-8000-00000000a001', 'aaaaaaa2-0000-4000-8000-000000000002',
   'confirmed', '{"directions": 2}'::jsonb),
  ('4e415e00-0000-4000-8000-00000000b001', 'bbbbbbb2-0000-4000-8000-000000000002',
   'confirmed', '{"directions": 1}'::jsonb);

insert into career_directions (review_id, rank, onet_soc_code, local_title,
                               rationale, student_selected) values
  ('4e415e00-0000-4000-8000-00000000a001', 1, '29-1141.00', 'Nursing',
   'Matches both her stated goal and the Social component of her profile.', true),
  ('4e415e00-0000-4000-8000-00000000a001', 2, '15-1252.00', 'Health informatics',
   'Keeps the medical context while using the Investigative strength.', false),
  ('4e415e00-0000-4000-8000-00000000b001', 1, '11-1021.00', 'Business management',
   'Consistent with a clear Enterprising profile.', false);

-- ── reports ──────────────────────────────────────────────────────────────
-- ORDER IS LOAD-BEARING. `trg_enforce_review_before_student_report` fires
-- before insert (and, since 0004, before update) and rejects a student report
-- whose session has no confirmed review. Move this block above the `reviews`
-- insert and the seed fails with "R9 violation" — which is the trigger working,
-- not a bug in the seed.
insert into reports (kind, session_id, cohort_id, storage_path,
                     template_version, engine_version) values
  ('student', '5e551011-0000-4000-8000-00000000a001', null,
   'reports/demo-a/fatima.pdf', '1.0.0', '1.0.0'),
  ('student', '5e551011-0000-4000-8000-00000000b001', null,
   'reports/demo-b/iqra.pdf',   '1.0.0', '1.0.0'),
  ('cohort',  null, 'c0000001-0000-4000-8000-00000000000a',
   'reports/demo-a/cohort.pdf', '1.0.0', '1.0.0'),
  ('cohort',  null, 'c0000002-0000-4000-8000-00000000000b',
   'reports/demo-b/cohort.pdf', '1.0.0', '1.0.0');

-- ── service-role-only tables ─────────────────────────────────────────────
-- `jobs` and `audit_log` have RLS enabled and no policy at all (0002_rls.sql,
-- deliberately). These rows exist so that "an authenticated user sees zero
-- rows" is a statement about the policy rather than about an empty table.
insert into jobs (kind, payload, status) values
  ('notify_psychologist',
   '{"session_id": "5e551011-0000-4000-8000-00000000a002"}'::jsonb, 'pending'),
  ('render_student',
   '{"session_id": "5e551011-0000-4000-8000-00000000a001"}'::jsonb, 'done');

insert into audit_log (actor, action, subject, meta) values
  ('aaaaaaa3-0000-4000-8000-000000000003', 'seed.bootstrap', 'organisations',
   '{"note": "demo data"}'::jsonb),
  ('bbbbbbb1-0000-4000-8000-000000000001', 'seed.bootstrap', 'organisations',
   '{"note": "demo data"}'::jsonb);
