"""The `render_student` handler (plan §14, M9).

Three things this file exists to hold:

* **R9 at the handler.** A session without a confirmed review must not produce
  a rendered document at all — not merely fail at the `reports` insert, by which
  point the PDF has been built and uploaded.
* **Idempotency.** The runner retries a job whose worker died after the work but
  before `complete_job`. Every step here must tolerate having already run, or a
  retry double-mails a student.
* **Order.** The upload happens before the `reports` row. A row promising a PDF
  that does not exist is a broken link in a student's inbox; an orphaned object
  is a few hundred kilobytes nobody sees.

WeasyPrint is stubbed. The PDF bytes are not what is under test here — the
document's *content* is asserted in `test_report_student.py` against HTML, which
needs no font stack, and the byte-level render is exercised by CI's Linux job
where the Pango libraries actually exist.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.jobs.handlers import HandlerError
from app.jobs.handlers import render as render_handler
from app.jobs.queue import Job

SETTINGS = Settings(report_storage_bucket="reports", report_url_days=7)

SESSION = "5e551011-0000-4000-8000-00000000a001"


def job(session_id: str | None = SESSION) -> Job:
    payload = {"session_id": session_id} if session_id else {}
    return Job(id="job-render-1", kind="render_student", payload=payload, attempts=1)


# ── a fake Supabase client, recording writes ─────────────────────────────────


class FakeTable:
    def __init__(self, store: FakeClient, name: str) -> None:
        self._store = store
        self._name = name
        self._filters: dict[str, object] = {}

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, column, value):
        self._filters[column] = value
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args):
        return self

    def insert(self, row):
        self._store.inserted.append((self._name, row))
        self._store.rows.setdefault(self._name, []).append({**row, "id": f"{self._name}-new"})
        return self

    def execute(self):
        if self._store.inserted and self._store.inserted[-1][0] == self._name:
            # An insert's execute returns the written row.
            return FakeResult([self._store.rows[self._name][-1]])

        rows = self._store.rows.get(self._name, [])
        for column, value in self._filters.items():
            key = column.split("->>")[-1] if "->>" in column else column
            rows = [
                row
                for row in rows
                if (row.get("payload", {}).get(key) if "->>" in column else row.get(key)) == value
            ]
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

    def create_signed_url(self, path, seconds):
        return {"signedURL": f"https://storage.test/{path}?token=abc&exp={seconds}"}


class FakeStorage:
    def __init__(self, store: FakeClient) -> None:
        self._store = store

    def from_(self, bucket):
        self._store.bucket = bucket
        return FakeBucket(self._store)


class FakeRpc:
    """Stands in for `record_student_report` (0010_student_reports.sql).

    Reimplements its idempotency here rather than just recording the call,
    because the handler's contract is "calling twice is safe" and a stub that
    always succeeded would let a regression through. The SQL itself is asserted
    against real Postgres in `tests/db/test_student_report.py`.
    """

    def __init__(self, store: FakeClient, name: str, params: dict) -> None:
        self._store = store
        self._name = name
        self._params = params

    def execute(self):
        self._store.rpc_calls.append((self._name, self._params))

        if self._name != "record_student_report":
            return FakeResult(None)

        session_id = self._params["p_session_id"]
        key = (session_id, self._params["p_template_version"])
        if key not in self._store.reports:
            self._store.reports[key] = f"report-{len(self._store.reports) + 1}"
            self._store.emails.append(session_id) if session_id not in self._store.emails else None
        return FakeResult(self._store.reports[key])


class FakeClient:
    def __init__(self, rows: dict[str, list[dict]]) -> None:
        self.rows = rows
        self.inserted: list[tuple[str, dict]] = []
        self.uploads: list[tuple] = []
        self.upload_raises = False
        self.rpc_raises = False
        self.bucket: str | None = None
        self.storage = FakeStorage(self)
        self.rpc_calls: list[tuple[str, dict]] = []
        self.reports: dict[tuple[str, str], str] = {}
        self.emails: list[str] = []

    def table(self, name):
        return FakeTable(self, name)

    def rpc(self, name, params):
        if self.rpc_raises:
            raise RuntimeError("R9: session has no confirmed review")
        return FakeRpc(self, name, params)


def client_with(review_status: str = "confirmed", **overrides) -> FakeClient:
    rows = {
        "sessions": [{"id": SESSION, "participant_id": "p-1"}],
        "participants": [
            {"id": "p-1", "full_name": "Fatima Khan", "email": "f@demo.test",
             "cohort_id": "c-1", "status": "confirmed"}
        ],
        "cohorts": [{"id": "c-1", "name": "Pre-Medical A", "organisation_id": "o-1"}],
        "organisations": [
            {"id": "o-1", "name": "Demo Academy", "brand_hex": "#1C6A61", "logo_path": None}
        ],
        "reviews": [
            {"id": "r-1", "session_id": SESSION, "status": review_status,
             "confirmed_at": "2026-03-12T09:14:22+00:00"}
        ],
        "scores": [
            {
                "session_id": SESSION,
                "engine_version": "1.0.0",
                "scored_at": "2026-03-11T10:00:00+00:00",
                "interests": {"raw": {"R": 2, "I": 8, "A": 3, "S": 7, "E": 4, "C": 6},
                              "code": "ISC", "band": "moderate"},
                "personality": {"raw": {"O": 41, "C": 33, "E": 22, "A": 38, "S": 27},
                                "bands": {"O": "high", "C": "average", "E": "low",
                                          "A": "high", "S": "low"}},
                "values_scores": {"instrument": None, "status": "module_not_administered"},
                # Present in the row and deliberately never loaded (R8).
                "flags": [{"code": "straightlining", "severity": "warn",
                           "detail": "31 identical consecutive answers"}],
            }
        ],
        "occupation_matches": [
            {"session_id": SESSION, "engine_version": "1.0.0", "rank": 1,
             "onet_soc_code": "29-1141.00", "local_title": "Registered Nurse",
             "local_pathway": "FSc Pre-Medical → BSc Nursing",
             "occupations": {"title": "Registered Nurses"}}
        ],
        "career_directions": [
            {"review_id": "r-1", "rank": 1, "onet_soc_code": "29-1141.00",
             "local_title": "Nursing", "rationale": "Matches her stated goal."}
        ],
        "reports": [],
        "jobs": [],
    }
    rows.update(overrides)
    return FakeClient(rows)


@pytest.fixture
def written(monkeypatch):
    """A fully written interpretations file and a stubbed WeasyPrint.

    The real `interpretations.yaml` ships empty (R3), so every render in this
    file would otherwise raise `InterpretationMissing` — which is the correct
    production behaviour and useless for testing the handler around it.
    """
    from tests.test_report_student import _written

    monkeypatch.setattr("app.content.load_interpretations", lambda *a, **k: _written())
    monkeypatch.setattr("app.report.render.render_pdf", lambda context: b"%PDF-1.7 fake")
    return _written()


# ── R9 ───────────────────────────────────────────────────────────────────────


def test_a_session_without_a_confirmed_review_never_renders(written):
    """R9 at the earliest point it can be checked.

    The database trigger would refuse the `reports` insert anyway — but by then
    the PDF has been built and uploaded to storage, which means an unreviewed
    minor's full profile exists as an object. Refusing at load time means it
    never gets written at all.
    """
    client = client_with(review_status="in_progress")

    with pytest.raises(HandlerError, match="no confirmed review"):
        render_handler.handle(client, SETTINGS, job())

    assert client.uploads == [], "a PDF was built for an unconfirmed session"
    assert client.rpc_calls == [], "a reports row was written for an unconfirmed session"


def test_a_session_with_no_review_at_all_never_renders(written):
    client = client_with(reviews=[])

    with pytest.raises(HandlerError, match="no confirmed review"):
        render_handler.handle(client, SETTINGS, job())

    assert client.uploads == []


def test_a_job_without_a_session_id_fails_with_a_described_error(written):
    with pytest.raises(HandlerError, match="no session_id"):
        render_handler.handle(client_with(), SETTINGS, job(session_id=None))


# ── the happy path, in order ─────────────────────────────────────────────────


def test_a_confirmed_session_uploads_then_records_then_queues_the_email(written):
    client = client_with()
    render_handler.handle(client, SETTINGS, job())

    assert len(client.uploads) == 1
    path, contents, options = client.uploads[0]
    assert contents == b"%PDF-1.7 fake"
    assert options["content-type"] == "application/pdf"
    assert options["x-upsert"] == "true", "a retry must overwrite, not collide"
    assert client.bucket == "reports"

    # One transaction writes the row and queues the email
    # (`record_student_report`, 0010_student_reports.sql).
    assert len(client.rpc_calls) == 1
    name, params = client.rpc_calls[0]
    assert name == "record_student_report"
    assert params["p_session_id"] == SESSION
    assert params["p_storage_path"] == path
    assert params["p_template_version"] == "1.0.0"
    assert client.emails == [SESSION]


def test_the_storage_path_does_not_carry_the_student_name():
    """Object keys appear in storage logs and inside the signed URL itself.

    A URL ending `fatima-khan.pdf` tells anyone it is forwarded to who the
    report is about, before they even open it.
    """
    path = render_handler.storage_path("Demo Academy", SESSION)

    assert path == f"demo-academy/{SESSION}.pdf"
    assert "fatima" not in path.lower()


def test_the_storage_path_is_stable_so_a_retry_overwrites():
    first = render_handler.storage_path("Demo Academy", SESSION)
    second = render_handler.storage_path("Demo Academy", SESSION)
    assert first == second


def test_an_awkward_organisation_name_still_produces_a_usable_key():
    path = render_handler.storage_path('St. Mary\'s "Convent" & College', SESSION)
    assert path.startswith("st-mary-s-convent-college/")
    assert '"' not in path and "&" not in path


# ── idempotency ──────────────────────────────────────────────────────────────


def test_a_retry_does_not_write_a_second_report_row_or_a_second_email(written):
    """The failure this prevents: a student gets two emails with two links.

    The runner retries a job whose process died between the work and
    `complete_job` — the single most likely thing to happen on free-tier
    compute, per 0008_job_runner.sql's header.
    """
    client = client_with()

    render_handler.handle(client, SETTINGS, job())
    render_handler.handle(client, SETTINGS, job())

    assert len(client.reports) == 1, "a retry wrote a second reports row"
    assert client.emails == [SESSION], "a retry queued a second email to the student"
    # The upload runs again on purpose — it upserts, and re-uploading identical
    # bytes is cheaper than a check that could go stale.
    assert len(client.uploads) == 2


def test_an_upload_failure_leaves_no_report_row_promising_a_missing_pdf(written):
    """Order matters: upload, then record.

    A `reports` row written first and an upload that then failed is a link in a
    student's inbox that resolves to nothing — and `reports.storage_path` is the
    only pointer, so nothing downstream can tell the difference.
    """
    client = client_with()
    client.upload_raises = True

    with pytest.raises(HandlerError, match="storage bucket 'reports'"):
        render_handler.handle(client, SETTINGS, job())

    assert client.rpc_calls == []
    assert client.emails == []


def test_a_failed_record_is_described_rather_than_raised_raw(written):
    """`record_student_report` raises on R9 — the message must survive.

    It lands in `jobs.last_error` and then in an operator's inbox. A bare
    exception type there says something broke; the SQL's own message says which
    rule refused and for which session.
    """
    client = client_with()
    client.rpc_raises = True

    with pytest.raises(HandlerError, match="no confirmed review"):
        render_handler.handle(client, SETTINGS, job())


# ── R3 ───────────────────────────────────────────────────────────────────────


def test_unwritten_interpretation_text_fails_the_job_rather_than_shipping_a_gap(monkeypatch):
    """R3, at the job level.

    Deliberately NOT caught by the handler. A psychologist not having written a
    paragraph yet should surface as a failed job that alerts — that is how it
    becomes someone's task, rather than a blank space on a student's page that
    nobody notices.
    """
    from app.content import InterpretationMissing

    monkeypatch.setattr("app.report.render.render_pdf", lambda context: b"%PDF")

    with pytest.raises(InterpretationMissing):
        render_handler.handle(client_with(), SETTINGS, job())


# ── R8, end to end through the loader ────────────────────────────────────────


def test_flags_in_the_scores_row_never_reach_the_loaded_report(written):
    """The `scores` row in this fixture carries a straightlining flag.

    `load_student_report` must not select it. This is the boundary R8 lives on:
    the flag exists, the psychologist saw it on their screen, and the student's
    document has no field for it.
    """
    from app.repository import load_student_report

    report = load_student_report(client_with(), SESSION)

    assert not hasattr(report, "flags")
    assert "straightlining" not in repr(report)
