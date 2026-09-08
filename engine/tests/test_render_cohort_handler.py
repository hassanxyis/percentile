"""The `render_cohort` handler (plan §10, §14, M11).

Three things this file exists to hold:

* **Order.** The upload happens before the `reports` row, for the reason
  `render.py` gives: `storage_path` is the only pointer, so a row written first
  and an upload that then failed is a download button resolving to nothing.
* **Idempotency.** The runner retries a job whose worker died after the work but
  before `complete_job`. A retry must rewrite its own row, not add a second.
* **No email.** The structural difference from `render_student`, asserted rather
  than left to be inferred — the failure mode is an institutional document
  mailed to whichever address the student handler's shape suggested.

WeasyPrint is stubbed. The document's content is asserted in
`test_report_cohort.py` against HTML, which needs no font stack; the byte-level
render is exercised by CI's Linux job where the Pango libraries exist.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.jobs.handlers import HandlerError, UnknownJobKind, handler_for
from app.jobs.handlers import render_cohort as handler
from app.jobs.queue import Job
from app.scoring.interests import RIASEC

SETTINGS = Settings(report_storage_bucket="reports")

COHORT = "c0000001-0000-4000-8000-00000000000a"


def job(cohort_id: str | None = COHORT) -> Job:
    payload = {"cohort_id": cohort_id} if cohort_id else {}
    return Job(id="job-cohort-1", kind="render_cohort", payload=payload, attempts=1)


# ── a fake Supabase client ───────────────────────────────────────────────────


class FakeQuery:
    def __init__(self, store: FakeClient, name: str) -> None:
        self._store = store
        self._name = name
        self._filters: dict[str, object] = {}

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, column, value):
        self._filters[column] = value
        return self

    def in_(self, column, values):
        self._filters[column] = ("in", list(values))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def execute(self):
        rows = self._store.rows.get(self._name, [])
        for column, value in self._filters.items():
            if isinstance(value, tuple) and value and value[0] == "in":
                rows = [row for row in rows if row.get(column) in value[1]]
            else:
                rows = [row for row in rows if row.get(column) == value]
        return FakeResult(rows)


class FakeResult:
    def __init__(self, data) -> None:
        self.data = data


class FakeBucket:
    def __init__(self, store: FakeClient) -> None:
        self._store = store

    def upload(self, path, contents, options):
        if self._store.upload_raises:
            raise RuntimeError("Bucket not found")
        self._store.uploads.append((path, contents, options))

    def get_public_url(self, path):
        return f"https://storage.test/branding/{path}"


class FakeStorage:
    def __init__(self, store: FakeClient) -> None:
        self._store = store

    def from_(self, bucket):
        self._store.bucket = bucket
        return FakeBucket(self._store)


class FakeRpc:
    """Stands in for `record_cohort_report` (0011_cohort_reports.sql).

    Reimplements its idempotency rather than merely recording the call: the
    handler's contract is "calling twice is safe", and a stub that always
    succeeded would let a regression through. The SQL itself is asserted against
    real Postgres in `tests/db/test_cohort_report.py`.
    """

    def __init__(self, store: FakeClient, name: str, params: dict) -> None:
        self._store = store
        self._name = name
        self._params = params

    def execute(self):
        self._store.rpc_calls.append((self._name, self._params))

        if self._name != "record_cohort_report":
            return FakeResult(None)

        key = (self._params["p_cohort_id"], self._params["p_template_version"])
        if key not in self._store.reports:
            self._store.reports[key] = f"report-{len(self._store.reports) + 1}"
        return FakeResult(self._store.reports[key])


class FakeClient:
    def __init__(self, rows: dict[str, list[dict]]) -> None:
        self.rows = rows
        self.uploads: list[tuple] = []
        self.upload_raises = False
        self.rpc_raises = False
        self.bucket: str | None = None
        self.storage = FakeStorage(self)
        self.rpc_calls: list[tuple[str, dict]] = []
        self.reports: dict[tuple[str, str], str] = {}

    def table(self, name):
        return FakeQuery(self, name)

    def rpc(self, name, params):
        if self.rpc_raises:
            raise RuntimeError("no cohort")
        return FakeRpc(self, name, params)


def score_row(session_id: str, interests: dict[str, int], flags=None) -> dict:
    return {
        "session_id": session_id,
        "engine_version": "1.0.0",
        "scored_at": "2026-03-11T10:00:00+00:00",
        "interests": {
            "raw": {scale: interests.get(scale, 0) for scale in RIASEC},
            "code": "ISC",
            "band": "well_differentiated",
        },
        "personality": {"bands": dict.fromkeys("OCEAS", "average")},
        "flags": flags if flags is not None else [],
    }


def client_with(**overrides) -> FakeClient:
    rows = {
        "cohorts": [
            {
                "id": COHORT,
                "name": "Class of 2027 — Pre-Medical A",
                "intake_year": 2027,
                "education_level": "intermediate",
                "organisation_id": "o-1",
            }
        ],
        "organisations": [
            {"id": "o-1", "name": "Demo Academy", "brand_hex": "#1C6A61", "logo_path": None}
        ],
        "participants": [
            {
                "id": f"p-{n}",
                "cohort_id": COHORT,
                "full_name": f"Student {n}",
                "intended_field": "medicine",
                "status": "confirmed",
            }
            for n in (1, 2, 3)
        ],
        "sessions": [
            {"id": f"s-{n}", "participant_id": f"p-{n}"} for n in (1, 2, 3)
        ],
        "scores": [
            score_row(f"s-{n}", {"I": 8, "S": 6, "C": 4}) for n in (1, 2, 3)
        ],
    }
    rows.update(overrides)
    return FakeClient(rows)


@pytest.fixture
def stub_pdf(monkeypatch):
    """WeasyPrint stubbed — the bytes are not what is under test here."""
    monkeypatch.setattr(
        "app.report.render.render_pdf", lambda context, *a, **k: b"%PDF-1.7 fake"
    )


# ── the registry ─────────────────────────────────────────────────────────────


def test_render_cohort_is_no_longer_unimplemented():
    """M11's headline: the last job kind in `NOT_YET_IMPLEMENTED` gains a handler.

    Asserted through `handler_for` rather than by reading the dict, because the
    failure this catches is a handler written but never registered — which looks
    exactly like the milestone being done until a tick claims the job.
    """
    assert handler_for("render_cohort") is handler.handle


def test_an_unimplemented_kind_still_names_its_milestone():
    """The mechanism must survive M11 removing one entry from it."""
    with pytest.raises(UnknownJobKind, match="M13"):
        handler_for("recompute_norms")


# ── the happy path, in order ─────────────────────────────────────────────────


def test_a_cohort_uploads_then_records(stub_pdf):
    client = client_with()
    handler.handle(client, SETTINGS, job())

    assert len(client.uploads) == 1
    path, contents, options = client.uploads[0]
    assert contents == b"%PDF-1.7 fake"
    assert options["content-type"] == "application/pdf"
    assert options["x-upsert"] == "true", "a re-render must overwrite, not collide"
    assert client.bucket == "reports"

    assert len(client.rpc_calls) == 1
    name, params = client.rpc_calls[0]
    assert name == "record_cohort_report"
    assert params["p_cohort_id"] == COHORT
    assert params["p_storage_path"] == path
    assert params["p_template_version"] == "1.0.0"


def test_no_email_is_queued_for_a_cohort_report(stub_pdf):
    """The structural difference from `render_student`.

    §15 lists no cohort delivery template and a cohort has no single recipient.
    The only write this handler makes is the one RPC.
    """
    client = client_with()
    handler.handle(client, SETTINGS, job())

    assert [name for name, _ in client.rpc_calls] == ["record_cohort_report"]


def test_a_retry_does_not_write_a_second_report_row(stub_pdf):
    """The runner retries a job whose process died between the work and
    `complete_job` — the most likely failure on free-tier compute."""
    client = client_with()

    handler.handle(client, SETTINGS, job())
    handler.handle(client, SETTINGS, job())

    assert len(client.reports) == 1, "a retry wrote a second reports row"
    # The upload runs again on purpose: it upserts, and re-uploading identical
    # bytes is cheaper than a check that could go stale.
    assert len(client.uploads) == 2


def test_an_upload_failure_leaves_no_row_promising_a_missing_pdf(stub_pdf):
    """Order matters: upload, then record.

    `reports.storage_path` is the only pointer, so a row written first and an
    upload that then failed is a download button that resolves to nothing — and
    nothing downstream can tell the difference.
    """
    client = client_with()
    client.upload_raises = True

    with pytest.raises(HandlerError, match="storage bucket 'reports'"):
        handler.handle(client, SETTINGS, job())

    assert client.rpc_calls == []


def test_a_failed_record_is_described_rather_than_raised_raw(stub_pdf):
    """The message lands in `jobs.last_error` and then in an operator's inbox."""
    client = client_with()
    client.rpc_raises = True

    with pytest.raises(HandlerError, match="could not record the report"):
        handler.handle(client, SETTINGS, job())


# ── failures that must be described ──────────────────────────────────────────


def test_a_job_without_a_cohort_id_fails_with_a_described_error():
    with pytest.raises(HandlerError, match="no cohort_id"):
        handler.handle(client_with(), SETTINGS, job(cohort_id=None))


def test_a_cohort_nobody_has_finished_fails_by_name(stub_pdf):
    """A cohort report over nobody is not a small report, it is a false one.

    Retrying is right — students may still submit — so this fails through the
    normal path and backs off rather than being dropped.
    """
    client = client_with(scores=[])

    with pytest.raises(HandlerError, match="no scored participants"):
        handler.handle(client, SETTINGS, job())

    assert client.uploads == []


def test_an_unknown_cohort_fails_before_anything_is_built(stub_pdf):
    client = client_with(cohorts=[])

    with pytest.raises(HandlerError, match="no cohort"):
        handler.handle(client, SETTINGS, job())

    assert client.uploads == []


# ── the storage key ──────────────────────────────────────────────────────────


def test_the_storage_path_is_keyed_on_the_cohort_id_not_its_name():
    """Object keys appear in storage logs and inside the signed URL itself.

    A cohort is named things like "Class of 2027 — Pre-Medical A"; a URL
    carrying that tells anyone it reaches which class the document is about.
    """
    path = handler.storage_path("Demo Academy", COHORT)

    assert path == f"demo-academy/cohort-{COHORT}.pdf"
    assert "medical" not in path.lower()


def test_the_storage_path_is_stable_so_a_re_render_overwrites():
    assert handler.storage_path("Demo Academy", COHORT) == handler.storage_path(
        "Demo Academy", COHORT
    )


def test_a_cohort_key_cannot_collide_with_a_student_one():
    """Both live in the same bucket under the same organisation slug."""
    from app.jobs.handlers.render import storage_path as student_path

    assert handler.storage_path("Demo Academy", COHORT) != student_path(
        "Demo Academy", COHORT
    )


def test_an_awkward_organisation_name_still_produces_a_usable_key():
    path = handler.storage_path('St. Mary\'s "Convent" & College', COHORT)

    assert path.startswith("st-mary-s-convent-college/")
    assert '"' not in path and "&" not in path


# ── R8, through the loader ───────────────────────────────────────────────────


def test_flag_detail_strings_are_dropped_at_the_loader(stub_pdf):
    """R8's boundary for this document, enforced where the data enters.

    The `scores` rows here carry full flag dicts. `Participant` keeps the codes
    — §7.4 needs them to say which checks fired — and the detail sentence is
    dropped at `load_cohort_report`, so no layer above can print "31 identical
    consecutive answers" beside a student's name.
    """
    from app.repository import load_cohort_report

    flagged = [
        {
            "code": "straightlining",
            "severity": "warn",
            "detail": "31 identical consecutive answers",
        },
        {"code": "too_fast", "severity": "warn", "detail": "2.1 min total"},
    ]
    client = client_with(
        scores=[
            score_row("s-1", {"I": 8, "S": 6}),
            score_row("s-2", {"I": 8, "S": 6}),
            score_row("s-3", {"I": 8, "S": 6}, flags=flagged),
        ]
    )

    report = load_cohort_report(client, COHORT)

    assert "31 identical consecutive answers" not in repr(report)
    assert report.aggregate.excluded == 1
    assert report.aggregate.excluded_by_code == {"straightlining": 1, "too_fast": 1}


def test_the_loader_reads_the_latest_score_per_session(stub_pdf):
    """R1: a rescore writes a new row beside the old one.

    Pinning to `SCORING_ENGINE_VERSION` would drop every session scored before
    the last bump out of the aggregate — silently shrinking the cohort rather
    than failing.
    """
    from app.repository import load_cohort_report

    old = score_row("s-1", {"R": 9})
    old["scored_at"] = "2026-01-01T00:00:00+00:00"
    new = score_row("s-1", {"I": 9})
    new["scored_at"] = "2026-03-01T00:00:00+00:00"

    client = client_with(
        sessions=[{"id": "s-1", "participant_id": "p-1"}],
        participants=[
            {
                "id": "p-1",
                "cohort_id": COHORT,
                "full_name": "Student 1",
                "intended_field": "medicine",
                "status": "confirmed",
            }
        ],
        scores=[old, new],
    )

    report = load_cohort_report(client, COHORT)

    assert report.aggregate.interest_distribution["I"] == 1
    assert report.aggregate.interest_distribution["R"] == 0
