"""The most important test in this repository (plan §17.1).

The O*NET Work Importance Locator user's guide publishes a worked example: a
filled-in card sort and the six value scores it produces. Reproducing that
example exactly is the *only* independent check that
`data/instruments/values_scoring.json` — which is transcribed by hand — is
correct. Nothing else in the test suite can catch a mistyped multiplier or an
item assigned to the wrong value.

A wrong transcription does not crash. It silently produces plausible work-value
scores for every student, forever.

This test skips until the two data files exist, and the skip is loud. Do not
delete it, and do not replace the expectation with whatever the code currently
outputs — that would make it a test of nothing.

To activate:
  1. Download the WIL user's guide and score report from onetcenter.org.
  2. Transcribe the scoring worksheet into data/instruments/values_scoring.json
     (item groupings, per-value multipliers, and published min/max).
  3. Copy the guide's worked example — the card placements and its six printed
     scores — into WORKED_EXAMPLE below, citing the page number.
  4. Run: pytest tests/test_values_worked_example.py
"""

import json
from pathlib import Path

import pytest

from app.scoring.values import score_values

DATA = Path(__file__).resolve().parents[2] / "data" / "instruments"
SCORING_JSON = DATA / "values_scoring.json"

# Fill both fields from the published guide, then set `source` to the page you
# took them from. Leave them empty and the test skips.
WORKED_EXAMPLE: dict = {
    "source": "",       # e.g. "WIL user's guide, p. 12, worked example"
    "sort": {},         # {item_code: column_no} exactly as printed
    "expected": {},     # {"ACH": 24, "IND": 18, ...} exactly as printed
}


pytestmark = pytest.mark.skipif(
    not SCORING_JSON.exists() or not WORKED_EXAMPLE["sort"],
    reason=(
        "WIL worked example not yet transcribed — work values are UNVERIFIED. "
        "See the module docstring. Until this test runs, no value score in any "
        "report can be trusted."
    ),
)


def test_values_worked_example():
    """Our scoring must reproduce the published example exactly."""
    config = json.loads(SCORING_JSON.read_text(encoding="utf-8"))

    result = score_values(WORKED_EXAMPLE["sort"], config)

    assert result["raw"] == WORKED_EXAMPLE["expected"], (
        "Scoring does not reproduce the published worked example. The "
        "transcription in values_scoring.json is wrong — check the multipliers "
        "and item groupings before changing any code in app/scoring/values.py."
    )


def test_worked_example_cites_its_source():
    """A transcribed expectation with no source cannot be re-checked later."""
    assert WORKED_EXAMPLE["source"], "record which page the example came from"
