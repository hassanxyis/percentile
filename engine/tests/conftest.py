"""Item and occupation builders for the scoring and matching tests.

Item *text* is never invented (R2), so these fixtures use placeholder text and
real structure: the scorers care about scale, keying and response range, and
nothing else. Real item text arrives in data/instruments/*.csv.

The occupation builders below are likewise synthetic. Real O*NET rows are
gitignored (each developer downloads their own), so the matching tests build
catalogues with hand-chosen interest vectors instead — which also lets a test
state the exact profile shape it is exercising.
"""

import pytest

from app.matching.occupations import Occupation
from app.scoring.get2 import GET2_SUBSCALES
from app.scoring.interests import RIASEC
from app.scoring.personality import BIG_FIVE
from app.scoring.types import Item


def interest_items(per_scale: int = 10) -> list[Item]:
    """60 interest items, 10 per RIASEC scale, checkbox 0..1 (plan §6.1, §7.1).

    The published instrument is a checkbox sheet, not a Likert scale: each
    item is checked or not, and a scale's score is its count of checks.
    """
    items = []
    ordinal = 1
    for scale in RIASEC:
        for n in range(1, per_scale + 1):
            items.append(
                Item(
                    code=f"IP_{scale}_{n:02d}",
                    ordinal=ordinal,
                    text=f"placeholder interest item {ordinal}",
                    scale=scale,
                    reverse_keyed=False,
                    response_min=0,
                    response_max=1,
                    instrument_code="interests",
                )
            )
            ordinal += 1
    return items


def personality_items(per_domain: int = 10) -> list[Item]:
    """50 personality items, 10 per domain, 1..5, half reverse-keyed.

    IPIP markers mix forward and reverse items within a domain; the alternating
    pattern here mirrors that without claiming to reproduce the real keying.
    """
    items = []
    ordinal = 1
    for scale in BIG_FIVE:
        for n in range(1, per_domain + 1):
            items.append(
                Item(
                    code=f"IPIP_{scale}_{n:02d}",
                    ordinal=ordinal,
                    text=f"placeholder personality item {ordinal}",
                    scale=scale,
                    reverse_keyed=(n % 2 == 0),
                    response_min=1,
                    response_max=5,
                    instrument_code="personality",
                )
            )
            ordinal += 1
    return items


def get2_scoring(per_scale: int = 4, max_raw: int = 8) -> dict:
    """A structurally valid stand-in for get2_scoring.json.

    Real item groupings and maxima are transcribed from Caird's published
    guide (R2); this exists only to exercise the code paths.
    """
    return {
        "subscales": {
            scale: {
                "items": [f"GET2_{scale.upper()[:3]}_{n:02d}" for n in range(1, per_scale + 1)],
                "max_raw": max_raw,
            }
            for scale in GET2_SUBSCALES
        }
    }


@pytest.fixture
def interests() -> list[Item]:
    return interest_items()


@pytest.fixture
def personality() -> list[Item]:
    return personality_items()


def occupation(
    onet_soc_code: str,
    interests: dict[str, float],
    job_zone: int = 4,
    pk_relevant: bool = False,
    entrepreneurial_track: bool = False,
) -> Occupation:
    """One synthetic occupation. `interests` may name only the scales that matter.

    Unnamed RIASEC scales default to 1.0 — the bottom of O*NET's 1..7
    Occupational Interests range, i.e. "this occupation does not involve that".
    """
    return Occupation(
        onet_soc_code=onet_soc_code,
        title=f"occupation {onet_soc_code}",
        job_zone=job_zone,
        interests={scale: float(interests.get(scale, 1.0)) for scale in RIASEC},
        pk_title=f"local title {onet_soc_code}" if pk_relevant else None,
        pk_pathway="Matric → some pathway" if pk_relevant else None,
        pk_relevant=pk_relevant,
        entrepreneurial_track=entrepreneurial_track,
    )


def occupation_catalogue() -> list[Occupation]:
    """A catalogue spanning every Job Zone and many SOC major groups.

    Three things this shape has to support:

    1. `diversify` — SOC group 15 is deliberately overstocked with six
       near-identical Investigative roles so the 2-per-group cap has something
       to bite on.
    2. The top-k cut — there must be **more than k=15 qualifying occupations
       that outrank the entrepreneurship track** for an Investigative/Realistic
       profile. Otherwise every track occupation lands in `matches` on cosine
       score alone and the §8 carve-out test proves nothing.
    3. Job Zone filtering — every band from 2 to 5 is represented.
    """
    return [
        # SOC 15 — six near-identical Investigative roles. Without the diversity
        # cap these would crowd out everything else for an I-heavy profile.
        occupation("15-1252.00", {"I": 7, "C": 5}, pk_relevant=True),
        occupation("15-1211.00", {"I": 7, "C": 5}),
        occupation("15-1212.00", {"I": 6, "C": 5}),
        occupation("15-1221.00", {"I": 6, "C": 4}),
        occupation("15-1231.00", {"I": 6, "C": 4}, job_zone=3),
        occupation("15-2041.00", {"I": 7, "C": 6}),
        # SOC 17 — Realistic/Investigative engineering.
        occupation("17-2141.00", {"R": 7, "I": 6}, pk_relevant=True),
        occupation("17-2051.00", {"R": 6, "I": 6}),
        # SOC 19 — Investigative science.
        occupation("19-2012.00", {"I": 7, "R": 4}),
        occupation("19-1042.00", {"I": 7, "R": 3}),
        occupation("19-4021.00", {"I": 6, "R": 5}, job_zone=3),
        # SOC 49 / 51 / 45 / 33 — Realistic trades and technical work. Two per
        # group so that an R/I profile can fill a full top-15 from high scorers
        # alone, the way it would against the real 923-row catalogue.
        occupation("49-3023.00", {"R": 7, "I": 4}, job_zone=3),
        occupation("49-9041.00", {"R": 7, "I": 4}, job_zone=3),
        occupation("51-4041.00", {"R": 7, "I": 3}, job_zone=3),
        occupation("51-8031.00", {"R": 7, "I": 4}, job_zone=3),
        occupation("45-2091.00", {"R": 7, "I": 3}, job_zone=3),
        occupation("45-2011.00", {"R": 7, "I": 3}, job_zone=3),
        occupation("33-2011.00", {"R": 6, "I": 4}, job_zone=3),
        occupation("33-3051.00", {"R": 6, "I": 4}, job_zone=3),
        # SOC 29 / 31 — Social, health care.
        occupation("29-1141.00", {"S": 7, "I": 5}, pk_relevant=True),
        occupation("31-9091.00", {"S": 6, "C": 4}, job_zone=3, pk_relevant=True),
        # SOC 25 — Social, teaching.
        occupation("25-2021.00", {"S": 7, "A": 4}, pk_relevant=True),
        # SOC 13 — Conventional.
        occupation("13-2011.00", {"C": 7, "E": 4}, pk_relevant=True),
        # SOC 27 — Artistic.
        occupation("27-1024.00", {"A": 7, "E": 3}),
        # SOC 47 — Realistic trades, lower Job Zone.
        occupation("47-2111.00", {"R": 7}, job_zone=3, pk_relevant=True),
        occupation("47-2031.00", {"R": 6}, job_zone=2),
        # SOC 11 / 41 — the entrepreneurship track. Purely Enterprising, so an
        # Investigative or Realistic profile scores them poorly on interest
        # alone — which is exactly the case the §8 carve-out exists for.
        occupation("11-1021.00", {"E": 7}, pk_relevant=True, entrepreneurial_track=True),
        occupation("41-1011.00", {"E": 7}, job_zone=2,
                   pk_relevant=True, entrepreneurial_track=True),
        occupation("11-9013.00", {"E": 7}, pk_relevant=True, entrepreneurial_track=True),
    ]


def uniform(items: list[Item], value: int) -> dict[str, int]:
    return {item.code: value for item in items}


def by_scale(items: list[Item], values: dict[str, int]) -> dict[str, int]:
    """Give every item in a scale the same response: {scale: value}."""
    return {item.code: values[item.scale] for item in items}
