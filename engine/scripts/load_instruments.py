"""Load instrument items from data/instruments/*.csv into the database (plan §6, M1).

Idempotent: rerunning after a CSV edit updates existing rows rather than
duplicating them, via upsert on each table's natural key. Items are read and
inserted verbatim — nothing here generates, paraphrases or reorders item text
(R2). An instrument whose CSV does not exist yet is skipped with a warning,
not an error: GET2's file is absent until its item count and response scale
are pinned from the primary source (plan §20 item 2, R10), and the rest of
the load must still succeed.

Usage:
    cd engine
    python scripts/load_instruments.py           # load everything present
    python scripts/load_instruments.py --dry-run  # parse and print counts only
"""

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "instruments"

REQUIRED_COLUMNS = (
    "code", "ordinal", "text", "scale", "response_min", "response_max", "reverse_keyed",
)


@dataclass(frozen=True)
class InstrumentSpec:
    code: str
    csv_filename: str
    title: str
    version: str
    source: str
    licence: str
    expected_item_count: int


INSTRUMENTS = (
    InstrumentSpec(
        code="interests",
        csv_filename="interests_items.csv",
        title="O*NET Interest Profiler Short Form",
        version="v1",
        source="onetcenter.org — National Center for O*NET Development",
        licence=(
            "Public domain, sponsored by the U.S. Department of Labor, Employment & "
            "Training Administration. Attribution required on every report (R6)."
        ),
        expected_item_count=60,
    ),
    InstrumentSpec(
        code="personality",
        csv_filename="personality_items.csv",
        title="IPIP Big-Five Factor Markers (50-item)",
        version="IPIP-50",
        source="ipip.ori.org (Goldberg, L. R., 1992, Psychological Assessment, 4, 26-42)",
        licence="Public domain — International Personality Item Pool.",
        expected_item_count=50,
    ),
    InstrumentSpec(
        code="get2",
        csv_filename="get2_items.csv",
        title="General Enterprising Tendency v2 (GET2)",
        version="v2",
        source="Caird, S. — The Open University (Open Research Online, oro.open.ac.uk)",
        licence=(
            "Provisional — pending written permission from The Open University / "
            "Dr. Sally Caird (R10). Do not use in sales material until confirmed."
        ),
        expected_item_count=0,  # unknown until pinned from the primary source, plan §20 item 2
    ),
)


def parse_items(spec: InstrumentSpec) -> list[dict]:
    """Read one instrument's CSV into item rows, validating shape as we go.

    Raises rather than guesses on anything malformed — a bad transcription
    that silently loads is worse than a script that refuses to run (same
    reasoning as the scoring modules' ScoringError).
    """
    path = DATA_DIR / spec.csv_filename
    rows = []

    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing_columns = set(REQUIRED_COLUMNS) - set(reader.fieldnames or [])
        if missing_columns:
            raise ValueError(f"{path.name}: missing columns {sorted(missing_columns)}")

        for line_no, row in enumerate(reader, start=2):
            try:
                rows.append(
                    {
                        "instrument_code": spec.code,
                        "ordinal": int(row["ordinal"]),
                        "code": row["code"].strip(),
                        "text": row["text"],
                        "scale": row["scale"].strip(),
                        "reverse_keyed": _parse_bool(row["reverse_keyed"]),
                        "response_min": int(row["response_min"]),
                        "response_max": int(row["response_max"]),
                    }
                )
            except (KeyError, ValueError) as exc:
                raise ValueError(f"{path.name}:{line_no}: {exc}") from exc

    _validate_unique_codes(spec, rows)
    return rows


def _parse_bool(value: str) -> bool:
    normalised = value.strip().lower()
    if normalised in ("true", "1", "yes"):
        return True
    if normalised in ("false", "0", "no"):
        return False
    raise ValueError(f"reverse_keyed value {value!r} is not a recognised boolean")


def _validate_unique_codes(spec: InstrumentSpec, rows: list[dict]) -> None:
    codes = [r["code"] for r in rows]
    if len(codes) != len(set(codes)):
        duplicates = sorted({c for c in codes if codes.count(c) > 1})
        raise ValueError(f"{spec.csv_filename}: duplicate item codes {duplicates}")


def load_instrument(client, spec: InstrumentSpec, dry_run: bool) -> int:
    path = DATA_DIR / spec.csv_filename
    if not path.exists():
        print(f"skip {spec.code}: {path.name} not found (not yet transcribed)")
        return 0

    items = parse_items(spec)
    print(f"{spec.code}: parsed {len(items)} items from {path.name}")

    if dry_run:
        return len(items)

    client.table("instruments").upsert(
        {
            "code": spec.code,
            "title": spec.title,
            "version": spec.version,
            "source": spec.source,
            "licence": spec.licence,
            "item_count": len(items),
        },
        on_conflict="code",
    ).execute()

    client.table("items").upsert(items, on_conflict="instrument_code,code").execute()
    print(f"{spec.code}: loaded {len(items)} items")
    return len(items)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="parse and print counts without writing to the DB"
    )
    args = parser.parse_args()

    client = None
    if not args.dry_run:
        from app.db import get_client

        client = get_client()

    total = 0
    for spec in INSTRUMENTS:
        total += load_instrument(client, spec, dry_run=args.dry_run)

    print(f"total items: {total}")


if __name__ == "__main__":
    try:
        main()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
