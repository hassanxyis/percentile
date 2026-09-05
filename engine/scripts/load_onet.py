"""Load the O*NET occupation catalogue into the database (plan §6.4, M3).

Idempotent: rerunning after an O*NET release updates existing rows rather than
duplicating them, via upsert on `onet_soc_code`.

**The O*NET 31.0 file layout differs from what plan §6.4 describes.** That
section was written against an older release and lists
`occupation_data.csv, interests.csv, work_values.csv, job_zones.csv`. In 31.0:

  - RIASEC moved from `interests.csv` to `career_interest_types.csv`, as
    Element IDs 1.B.1.a-f on Scale ID "OI" (Occupational Interests, 1..7).
  - **Work Values was dropped from the database entirely.** There is no
    `work_values.csv` and no 1.B.2.* element anywhere in the release. The six
    `occupations.value_*` columns therefore stay NULL and this script does not
    write them. Nothing reads them: plan §7.3/§8 keep GET2 out of the cosine
    match, and v2 has no student values vector to compare against either way.
    The columns are left in place rather than dropped — migrations are
    append-only, and dropping them would not gain anything.

Occupations lacking a Job Zone or a complete RIASEC profile are skipped: both
are required to rank an occupation at all (`app/matching/occupations.py`).
In 31.0 that drops 93 of 1016 rows, leaving 923.

Usage:
    cd engine
    python scripts/load_onet.py             # load the catalogue, then apply the PK map
    python scripts/load_onet.py --dry-run   # parse and print counts only, no writes
"""

import argparse
import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ONET_DIR = REPO_ROOT / "data" / "onet"
PK_MAP_PATH = REPO_ROOT / "data" / "local" / "pk_occupation_map.csv"

# The archive extracts into a versioned directory (db_31_0_csv/), so the exact
# path changes every release. Glob for it rather than pinning one.
ONET_DIR_GLOB = "db_*_csv"

OCCUPATION_DATA = "occupation_data.csv"
JOB_ZONES = "job_zones.csv"
CAREER_INTEREST_TYPES = "career_interest_types.csv"

SOC_COLUMN = "O*NET-SOC Code"

# Scale ID "OI" is the 1..7 Occupational Interests rating. The same file also
# carries "IH" (interest high-point ranks) rows for elements 1.B.1.g-i, which
# are a different measure and must not be pivoted in alongside the six scales.
INTEREST_SCALE_ID = "OI"

# Element ID -> the RIASEC letter used everywhere else in the engine.
RIASEC_ELEMENTS = {
    "1.B.1.a": "R",
    "1.B.1.b": "I",
    "1.B.1.c": "A",
    "1.B.1.d": "S",
    "1.B.1.e": "E",
    "1.B.1.f": "C",
}

PK_MAP_COLUMNS = (
    "onet_soc_code",
    "pk_title",
    "pk_pathway",
    "pk_relevant",
    "entrepreneurial_track",
)


def onet_release_dir() -> Path:
    """The extracted O*NET release directory, newest first if several exist."""
    candidates = sorted(
        (p for p in ONET_DIR.glob(ONET_DIR_GLOB) if p.is_dir()),
        reverse=True,
    )
    if not candidates:
        raise ValueError(
            f"no O*NET release found in {ONET_DIR}. Download the database CSV "
            f"archive from onetcenter.org/database.html and extract it there "
            f"(expected a directory matching {ONET_DIR_GLOB!r}). The files are "
            f"gitignored, so each developer downloads their own copy."
        )
    return candidates[0]


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        raise ValueError(f"{path.name} not found in {path.parent}")
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _require_columns(path: Path, rows: list[dict], required: tuple[str, ...]) -> None:
    if not rows:
        raise ValueError(f"{path.name}: file is empty")
    missing = set(required) - set(rows[0])
    if missing:
        raise ValueError(f"{path.name}: missing columns {sorted(missing)}")


def parse_titles(release_dir: Path) -> dict[str, str]:
    path = release_dir / OCCUPATION_DATA
    rows = _read_csv(path)
    _require_columns(path, rows, (SOC_COLUMN, "Title"))
    return {row[SOC_COLUMN]: row["Title"] for row in rows}


def parse_job_zones(release_dir: Path) -> dict[str, int]:
    path = release_dir / JOB_ZONES
    rows = _read_csv(path)
    _require_columns(path, rows, (SOC_COLUMN, "Job Zone"))

    zones = {}
    for line_no, row in enumerate(rows, start=2):
        try:
            zones[row[SOC_COLUMN]] = int(row["Job Zone"])
        except ValueError as exc:
            raise ValueError(f"{path.name}:{line_no}: bad Job Zone {row['Job Zone']!r}") from exc
    return zones


def parse_interests(release_dir: Path) -> dict[str, dict[str, float]]:
    """Pivot the long-format interest file into {soc_code: {RIASEC letter: value}}."""
    path = release_dir / CAREER_INTEREST_TYPES
    rows = _read_csv(path)
    _require_columns(path, rows, (SOC_COLUMN, "Element ID", "Scale ID", "Data Value"))

    profiles: dict[str, dict[str, float]] = {}
    for line_no, row in enumerate(rows, start=2):
        if row["Scale ID"] != INTEREST_SCALE_ID:
            continue
        scale = RIASEC_ELEMENTS.get(row["Element ID"])
        if scale is None:
            continue

        try:
            value = float(row["Data Value"])
        except ValueError as exc:
            raise ValueError(
                f"{path.name}:{line_no}: bad Data Value {row['Data Value']!r}"
            ) from exc

        profiles.setdefault(row[SOC_COLUMN], {})[scale] = value

    return profiles


def parse_occupations(release_dir: Path) -> list[dict]:
    """Join titles, Job Zones and RIASEC profiles into `occupations` rows.

    An occupation missing a title, a Job Zone or its whole interest profile is
    skipped — it cannot be ranked, so loading it would only pad the catalogue.
    A *partial* interest profile is different: that means a truncated or
    corrupt download, and is raised rather than silently half-loaded.
    """
    titles = parse_titles(release_dir)
    zones = parse_job_zones(release_dir)
    profiles = parse_interests(release_dir)

    occupations = []
    for soc_code, profile in sorted(profiles.items()):
        if len(profile) != len(RIASEC_ELEMENTS):
            missing = sorted(set(RIASEC_ELEMENTS.values()) - set(profile))
            raise ValueError(
                f"{CAREER_INTEREST_TYPES}: {soc_code} has an incomplete interest "
                f"profile, missing {missing} — the download looks truncated"
            )
        if soc_code not in titles or soc_code not in zones:
            continue

        occupations.append(
            {
                "onet_soc_code": soc_code,
                "title": titles[soc_code],
                "job_zone": zones[soc_code],
                "interest_r": profile["R"],
                "interest_i": profile["I"],
                "interest_a": profile["A"],
                "interest_s": profile["S"],
                "interest_e": profile["E"],
                "interest_c": profile["C"],
                # value_ach..value_wcn deliberately absent — see the module
                # docstring. O*NET 31.0 does not publish Work Values.
            }
        )

    return occupations


def parse_pk_map() -> list[dict]:
    """Read the hand-curated Pakistan localisation layer (plan §6.4)."""
    if not PK_MAP_PATH.exists():
        print(f"skip pk map: {PK_MAP_PATH.name} not found")
        return []

    rows = _read_csv(PK_MAP_PATH)
    _require_columns(PK_MAP_PATH, rows, PK_MAP_COLUMNS)

    mapped = []
    for line_no, row in enumerate(rows, start=2):
        try:
            mapped.append(
                {
                    "onet_soc_code": row["onet_soc_code"].strip(),
                    "pk_title": row["pk_title"].strip(),
                    "pk_pathway": row["pk_pathway"].strip(),
                    "pk_relevant": _parse_bool(row["pk_relevant"]),
                    "entrepreneurial_track": _parse_bool(row["entrepreneurial_track"]),
                }
            )
        except ValueError as exc:
            raise ValueError(f"{PK_MAP_PATH.name}:{line_no}: {exc}") from exc

    codes = [r["onet_soc_code"] for r in mapped]
    if len(codes) != len(set(codes)):
        duplicates = sorted({c for c in codes if codes.count(c) > 1})
        raise ValueError(f"{PK_MAP_PATH.name}: duplicate SOC codes {duplicates}")

    return mapped


def _parse_bool(value: str) -> bool:
    normalised = value.strip().lower()
    if normalised in ("true", "1", "yes"):
        return True
    if normalised in ("false", "0", "no"):
        return False
    raise ValueError(f"{value!r} is not a recognised boolean")


def validate_pk_map(pk_rows: list[dict], occupations: list[dict]) -> None:
    """Every mapped SOC code must exist in the catalogue.

    A code that matches nothing is a typo in a hand-edited file, and would
    otherwise vanish silently — the localisation layer is the product's moat
    (plan §6.4), so a lost row is worth failing the load over.
    """
    known = {occ["onet_soc_code"] for occ in occupations}
    unknown = sorted(r["onet_soc_code"] for r in pk_rows if r["onet_soc_code"] not in known)
    if unknown:
        raise ValueError(
            f"{PK_MAP_PATH.name}: SOC codes not present in the O*NET catalogue: {unknown}. "
            f"Check for a typo, or for an occupation with no Job Zone / interest profile."
        )


def load(client, dry_run: bool) -> tuple[int, int]:
    release_dir = onet_release_dir()
    print(f"reading {release_dir.name}")

    occupations = parse_occupations(release_dir)
    print(f"occupations: parsed {len(occupations)} with a job zone and full interest profile")

    pk_rows = parse_pk_map()
    validate_pk_map(pk_rows, occupations)
    if pk_rows:
        local = sum(1 for r in pk_rows if r["pk_relevant"])
        track = sum(1 for r in pk_rows if r["entrepreneurial_track"])
        print(f"pk map: parsed {len(pk_rows)} rows ({local} pk_relevant, {track} entrepreneurial)")

    if dry_run:
        return len(occupations), len(pk_rows)

    # One upsert of COMPLETE rows, not two partial upserts. PostgREST will not
    # settle a partial payload against an existing key: `ON CONFLICT DO
    # UPDATE` assigns every omitted NOT NULL column to NULL ("null value in
    # column "title" violates not-null constraint"), even when the key already
    # exists. The two-upsert design (base rows, then pk-map rows) therefore
    # cannot run against Supabase at all — a re-load fails no matter what. So:
    # every row carries the columns `occupations` declares NOT NULL, and the
    # localisation layer is merged in before one upsert. A map fix now rewrites
    # the whole catalogue, which the old design avoided — but a loader that
    # runs is worth more than a cheaper one that errors.
    pk_by_code = {row["onet_soc_code"]: row for row in pk_rows}
    rows = []
    for occupation in occupations:
        row = {
            **occupation,
            "pk_relevant": False,
            "entrepreneurial_track": False,
            "pk_title": None,
            "pk_pathway": None,
        }
        row.update(pk_by_code.get(occupation["onet_soc_code"], {}))
        rows.append(row)

    client.table("occupations").upsert(rows, on_conflict="onet_soc_code").execute()
    if pk_rows:
        print(f"occupations: loaded {len(rows)} ({len(pk_rows)} localised rows)")

    return len(occupations), len(pk_rows)


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

    occupations, pk_rows = load(client, dry_run=args.dry_run)
    verb = "would load" if args.dry_run else "loaded"
    print(f"{verb} {occupations} occupations, {pk_rows} localisation rows")


if __name__ == "__main__":
    try:
        main()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
