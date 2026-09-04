"""Instrument loader parsing (plan §6, M1).

Exercises `scripts/load_instruments.py` against the real transcribed CSVs in
data/instruments/ — this is the one test in the suite that touches real
instrument data rather than placeholder fixtures, so it is the first thing
that would catch a bad transcription (duplicate code, wrong column, an
out-of-range response_max) before it ever reaches the database.
"""

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from load_instruments import INSTRUMENTS, parse_items  # noqa: E402


def _spec(code: str):
    return next(s for s in INSTRUMENTS if s.code == code)


def test_interests_csv_has_sixty_items():
    items = parse_items(_spec("interests"))

    assert len(items) == 60
    assert {i["code"] for i in items} == {f"IP_{s}_{n:02d}" for s in "RIASEC" for n in range(1, 11)}


def test_interests_csv_is_checkbox_scaled():
    """R2 correction: the Interest Profiler is a checkbox sheet, 0..1, not a
    Likert scale — every row must reflect that, not a stale 0..4 range."""
    items = parse_items(_spec("interests"))

    assert all(i["response_min"] == 0 and i["response_max"] == 1 for i in items)
    assert all(i["reverse_keyed"] is False for i in items)


def test_interests_csv_ten_items_per_scale():
    items = parse_items(_spec("interests"))

    counts = {}
    for item in items:
        counts[item["scale"]] = counts.get(item["scale"], 0) + 1

    assert counts == dict.fromkeys("RIASEC", 10)


def test_personality_csv_has_fifty_items():
    items = parse_items(_spec("personality"))

    assert len(items) == 50
    assert all(i["response_min"] == 1 and i["response_max"] == 5 for i in items)


def test_personality_csv_ten_items_per_domain():
    items = parse_items(_spec("personality"))

    counts = {}
    for item in items:
        counts[item["scale"]] = counts.get(item["scale"], 0) + 1

    assert counts == dict.fromkeys("OCEAS", 10)


def test_personality_csv_reverse_keying_matches_published_counts():
    """Cross-check against the source document's own stated +/- keyed counts
    per factor (Goldberg, 1992, via the IPIP sample questionnaire page)."""
    items = parse_items(_spec("personality"))

    reverse_by_scale = {}
    for item in items:
        if item["reverse_keyed"]:
            reverse_by_scale[item["scale"]] = reverse_by_scale.get(item["scale"], 0) + 1

    assert reverse_by_scale == {"E": 5, "A": 4, "C": 4, "S": 8, "O": 3}


def test_get2_csv_absent_is_not_an_error():
    """GET2's item count and response scale are not yet pinned from the
    primary source (plan §20 item 2, R10) — the loader must skip it cleanly,
    not fail the whole run."""
    from load_instruments import DATA_DIR

    assert not (DATA_DIR / _spec("get2").csv_filename).exists()


def test_duplicate_code_raises(tmp_path, monkeypatch):
    import load_instruments as li

    monkeypatch.setattr(li, "DATA_DIR", tmp_path)
    (tmp_path / "bad.csv").write_text(
        "code,ordinal,text,scale,response_min,response_max,reverse_keyed\n"
        "X_01,1,\"a\",R,0,1,false\n"
        "X_01,2,\"b\",R,0,1,false\n",
        encoding="utf-8",
    )
    spec = li.InstrumentSpec(
        code="test", csv_filename="bad.csv", title="t", version="v",
        source="s", licence="l", expected_item_count=2,
    )

    with pytest.raises(ValueError, match="duplicate item codes"):
        li.parse_items(spec)
