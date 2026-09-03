"""Item builders for the scoring tests.

Item *text* is never invented (R2), so these fixtures use placeholder text and
real structure: the scorers care about scale, keying and response range, and
nothing else. Real item text arrives in data/instruments/*.csv.
"""

import pytest

from app.scoring.interests import RIASEC
from app.scoring.personality import BIG_FIVE
from app.scoring.types import Item


def interest_items(per_scale: int = 10) -> list[Item]:
    """60 interest items, 10 per RIASEC scale, 0..4."""
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
                    response_max=4,
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


@pytest.fixture
def interests() -> list[Item]:
    return interest_items()


@pytest.fixture
def personality() -> list[Item]:
    return personality_items()


def uniform(items: list[Item], value: int) -> dict[str, int]:
    return {item.code: value for item in items}


def by_scale(items: list[Item], values: dict[str, int]) -> dict[str, int]:
    """Give every item in a scale the same response: {scale: value}."""
    return {item.code: values[item.scale] for item in items}
