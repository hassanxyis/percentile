"""O*NET catalogue loader (plan §6.4, M3).

Two layers here. The `tmp_path` tests build tiny synthetic O*NET files and run
everywhere, including CI. The real-data tests read `data/onet/`, which is
gitignored — each developer downloads their own copy — so they skip when it is
absent rather than failing a machine that never had it.
"""

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import load_onet  # noqa: E402
from load_onet import (  # noqa: E402
    RIASEC_ELEMENTS,
    onet_release_dir,
    parse_occupations,
    parse_pk_map,
    validate_pk_map,
)

OCCUPATION_DATA_CSV = (
    "O*NET-SOC Code,Title,Description\n"
    '11-1011.00,Chief Executives,"Determine and formulate policies."\n'
    '15-1252.00,Software Developers,"Develop applications."\n'
    '29-1141.00,Registered Nurses,"Assess patient health."\n'
)

JOB_ZONES_CSV = (
    "O*NET-SOC Code,Title,Job Zone,Date,Domain Source\n"
    "11-1011.00,Chief Executives,5,08/2023,Analyst\n"
    "15-1252.00,Software Developers,4,08/2023,Analyst\n"
    # 29-1141.00 deliberately absent — no Job Zone means it cannot be ranked.
)


def _interest_rows(soc_code: str, values: dict[str, float]) -> str:
    """Long-format interest rows for one occupation, in O*NET's own layout."""
    element_for = {letter: element for element, letter in RIASEC_ELEMENTS.items()}
    return "".join(
        f"{soc_code},Title,{element_for[letter]},{letter},OI,"
        f"Occupational Interests,{value},02/2026,Machine Learning/Expert\n"
        for letter, value in values.items()
    )


CAREER_INTEREST_HEADER = (
    "O*NET-SOC Code,Title,Element ID,Element Name,Scale ID,Scale Name,"
    "Data Value,Date,Domain Source\n"
)

FULL_PROFILE = {"R": 1.26, "I": 3.05, "A": 2.16, "S": 3.54, "E": 6.96, "C": 4.97}


def _release(tmp_path: Path, interests_csv: str) -> Path:
    release_dir = tmp_path / "db_31_0_csv"
    release_dir.mkdir()
    (release_dir / "occupation_data.csv").write_text(OCCUPATION_DATA_CSV, encoding="utf-8")
    (release_dir / "job_zones.csv").write_text(JOB_ZONES_CSV, encoding="utf-8")
    (release_dir / "career_interest_types.csv").write_text(interests_csv, encoding="utf-8")
    return release_dir


# ── the long -> wide pivot ───────────────────────────────────────────────────


def test_pivots_interest_rows_into_wide_columns(tmp_path):
    release = _release(
        tmp_path,
        CAREER_INTEREST_HEADER
        + _interest_rows("11-1011.00", FULL_PROFILE)
        + _interest_rows("15-1252.00", FULL_PROFILE),
    )

    occupations = parse_occupations(release)

    chief = next(o for o in occupations if o["onet_soc_code"] == "11-1011.00")
    assert chief["title"] == "Chief Executives"
    assert chief["job_zone"] == 5
    assert chief["interest_r"] == 1.26
    assert chief["interest_e"] == 6.96
    assert chief["interest_c"] == 4.97


def test_work_values_columns_are_not_written(tmp_path):
    """O*NET 31.0 dropped Work Values entirely — there is no file to read.

    The `occupations.value_*` columns stay NULL rather than being zero-filled;
    a zero would read as "this occupation scores nothing on achievement",
    which is a claim the data does not make.
    """
    release = _release(
        tmp_path, CAREER_INTEREST_HEADER + _interest_rows("11-1011.00", FULL_PROFILE)
    )

    occupations = parse_occupations(release)

    assert occupations
    assert not any(key.startswith("value_") for key in occupations[0])


def test_ignores_non_interest_scales(tmp_path):
    """The same file carries 'IH' high-point ranks on elements 1.B.1.g-i.

    Those are a different measure on a different scale and must not be pivoted
    in alongside the six RIASEC ratings.
    """
    noise = (
        "11-1011.00,Chief Executives,1.B.1.g,First Interest High-Point,IH,"
        "Occupational Interest High-Point,5.00,02/2026,Machine Learning/Expert\n"
        "11-1011.00,Chief Executives,1.B.1.a,Realistic,IH,"
        "Occupational Interest High-Point,9.99,02/2026,Machine Learning/Expert\n"
    )
    release = _release(
        tmp_path,
        CAREER_INTEREST_HEADER + noise + _interest_rows("11-1011.00", FULL_PROFILE),
    )

    occupations = parse_occupations(release)

    assert occupations[0]["interest_r"] == 1.26  # the OI row, not the IH row


# ── rows that cannot be ranked ───────────────────────────────────────────────


def test_occupation_without_a_job_zone_is_dropped(tmp_path):
    """29-1141.00 has a title and interests but no Job Zone row."""
    release = _release(
        tmp_path,
        CAREER_INTEREST_HEADER
        + _interest_rows("11-1011.00", FULL_PROFILE)
        + _interest_rows("29-1141.00", FULL_PROFILE),
    )

    codes = {o["onet_soc_code"] for o in parse_occupations(release)}

    assert "11-1011.00" in codes
    assert "29-1141.00" not in codes


def test_partial_interest_profile_raises(tmp_path):
    """A missing scale means a truncated download, not a skippable row.

    Loading five of six scales would silently distort every match against that
    occupation, so fail loudly instead.
    """
    release = _release(
        tmp_path,
        CAREER_INTEREST_HEADER + _interest_rows("11-1011.00", {"R": 1.26, "I": 3.05}),
    )

    with pytest.raises(ValueError, match="incomplete interest profile"):
        parse_occupations(release)


def test_missing_file_names_the_file(tmp_path):
    release = tmp_path / "db_31_0_csv"
    release.mkdir()
    (release / "occupation_data.csv").write_text(OCCUPATION_DATA_CSV, encoding="utf-8")

    with pytest.raises(ValueError, match="job_zones.csv not found"):
        parse_occupations(release)


def test_missing_release_directory_explains_the_download(tmp_path, monkeypatch):
    monkeypatch.setattr(load_onet, "ONET_DIR", tmp_path)

    with pytest.raises(ValueError, match="onetcenter.org"):
        onet_release_dir()


def test_newest_release_wins(tmp_path, monkeypatch):
    (tmp_path / "db_30_0_csv").mkdir()
    (tmp_path / "db_31_0_csv").mkdir()
    monkeypatch.setattr(load_onet, "ONET_DIR", tmp_path)

    assert onet_release_dir().name == "db_31_0_csv"


# ── the Pakistan localisation layer ──────────────────────────────────────────


def test_pk_map_unknown_soc_code_raises():
    """A code matching nothing is a typo in a hand-edited file.

    The localisation layer is the product's moat (plan §6.4); a row that
    silently maps nothing is worth failing the load over.
    """
    pk_rows = [{"onet_soc_code": "99-9999.00", "pk_title": "x", "pk_pathway": "y",
                "pk_relevant": True, "entrepreneurial_track": False}]
    occupations = [{"onet_soc_code": "15-1252.00"}]

    with pytest.raises(ValueError, match="not present in the O\\*NET catalogue"):
        validate_pk_map(pk_rows, occupations)


def test_pk_map_accepts_codes_present_in_the_catalogue():
    pk_rows = [{"onet_soc_code": "15-1252.00", "pk_title": "x", "pk_pathway": "y",
                "pk_relevant": True, "entrepreneurial_track": False}]

    validate_pk_map(pk_rows, [{"onet_soc_code": "15-1252.00"}])


def test_pk_map_duplicate_code_raises(tmp_path, monkeypatch):
    path = tmp_path / "pk_occupation_map.csv"
    path.write_text(
        "onet_soc_code,pk_title,pk_pathway,pk_relevant,entrepreneurial_track\n"
        '15-1252.00,"a","b",true,false\n'
        '15-1252.00,"c","d",true,false\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(load_onet, "PK_MAP_PATH", path)

    with pytest.raises(ValueError, match="duplicate SOC codes"):
        parse_pk_map()


def test_pk_map_bad_boolean_names_the_line(tmp_path, monkeypatch):
    path = tmp_path / "pk_occupation_map.csv"
    path.write_text(
        "onet_soc_code,pk_title,pk_pathway,pk_relevant,entrepreneurial_track\n"
        '15-1252.00,"a","b",maybe,false\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(load_onet, "PK_MAP_PATH", path)

    with pytest.raises(ValueError, match="pk_occupation_map.csv:2"):
        parse_pk_map()


def test_missing_pk_map_is_not_an_error(tmp_path, monkeypatch):
    """The catalogue still loads without a localisation layer."""
    monkeypatch.setattr(load_onet, "PK_MAP_PATH", tmp_path / "absent.csv")

    assert parse_pk_map() == []


# ── the committed map and the real download ──────────────────────────────────


def test_committed_pk_map_parses():
    """data/local/pk_occupation_map.csv is committed, so this always runs."""
    rows = parse_pk_map()

    assert rows, "the committed stub map should have rows"
    assert all(r["onet_soc_code"] for r in rows)
    assert any(r["entrepreneurial_track"] for r in rows), (
        "the §8 carve-out needs at least one entrepreneurship-track occupation"
    )


requires_onet = pytest.mark.skipif(
    not list(load_onet.ONET_DIR.glob(load_onet.ONET_DIR_GLOB)),
    reason="data/onet/ is gitignored; download the O*NET release to run this",
)


@requires_onet
def test_real_catalogue_loads():
    occupations = parse_occupations(onet_release_dir())

    assert len(occupations) > 800
    assert all(2 <= o["job_zone"] <= 5 for o in occupations)
    # O*NET's Occupational Interests scale is 1..7 (scales_reference.csv).
    assert all(1.0 <= o["interest_r"] <= 7.0 for o in occupations)


@requires_onet
def test_committed_pk_map_matches_the_real_catalogue():
    """Every hand-written SOC code must still exist after an O*NET release."""
    validate_pk_map(parse_pk_map(), parse_occupations(onet_release_dir()))
