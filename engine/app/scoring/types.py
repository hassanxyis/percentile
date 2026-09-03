"""Shared types for the scoring layer.

`Item` is deliberately a plain frozen dataclass rather than a database row: the
scoring modules take items and responses as arguments and touch no database
(plan §7). That is what makes them testable offline and rescoreable in bulk.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Item:
    """One instrument item, as loaded from data/instruments/*.csv.

    `response_min` / `response_max` come from the data file, not from a constant
    in code. Reverse-keying is computed from them so changing an instrument's
    response scale does not require touching the scorer (plan §7.2).
    """

    code: str
    ordinal: int
    text: str
    scale: str
    reverse_keyed: bool
    response_min: int
    response_max: int
    instrument_code: str = ""


class ScoringError(ValueError):
    """Raised when input cannot be scored.

    Always prefer raising over returning a partial or zero-filled result. A
    silently zero-filled score reaches a seventeen-year-old's report and nobody
    ever finds out it was wrong.
    """
