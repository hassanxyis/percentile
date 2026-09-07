"""Print which interpretation strings are still unwritten (R3).

    cd engine && python scripts/check_interpretations.py

Exists so the person filling in `app/content/interpretations.yaml` can see the
remaining work as a list, rather than discovering it one exception at a time by
running a render and reading `jobs.last_error`.

Exit code is 0 even when strings are missing. This is a worklist, not a gate:
the gate is `InterpretationMissing` at render time, which cannot be forgotten or
skipped with a flag. A script that failed CI here would only mean the repo could
not be committed to until a psychologist had finished writing, which is not the
constraint anyone wants.

`--keys` prints every key, written or not, which is what to paste into an email
when asking someone to fill the file in.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.content import (  # noqa: E402 — must follow the sys.path insert
    INTERPRETATIONS_PATH,
    load_interpretations,
)

# Keys the GET2 page asks for. Reported separately because R10 means the module
# is not administered today: these being blank is the expected state, and mixing
# them into the main list makes a finished file look unfinished.
GET2_PREFIX = "get2."


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keys", action="store_true", help="list every key, written or not"
    )
    args = parser.parse_args()

    interpretations = load_interpretations(INTERPRETATIONS_PATH)

    if args.keys:
        for key in sorted(_all_keys(interpretations)):
            written = "written" if interpretations.has(*key.split(".")) else "EMPTY"
            print(f"{written:8}  {key}")
        return 0

    missing = interpretations.missing_keys()
    get2 = [key for key in missing if key.startswith(GET2_PREFIX)]
    rest = [key for key in missing if not key.startswith(GET2_PREFIX)]

    print(f"{INTERPRETATIONS_PATH}\n")

    if not rest:
        print("All interpretation text for the live modules is written.")
    else:
        print(f"{len(rest)} string(s) still to write (R3 — a person, not a model):\n")
        for key in rest:
            print(f"  {key}")

    if get2:
        print(
            f"\n{len(get2)} GET2 string(s) unwritten. Expected: the module is not "
            "administered until permission is confirmed (R10, plan §20 item 1).\n"
            "The GET2 page is omitted entirely rather than shown blank, so these\n"
            "are not needed for a report to render today."
        )

    return 0


def _all_keys(interpretations) -> list[str]:
    """Every leaf key in the file, whether or not it holds text."""
    found: list[str] = []
    _walk(interpretations._data, (), found)  # noqa: SLF001 — this script is the file's tooling
    return found


def _walk(node, path: tuple[str, ...], out: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            _walk(value, (*path, str(key)), out)
    elif isinstance(node, list):
        out.append(".".join(path))
    else:
        out.append(".".join(path))


if __name__ == "__main__":
    raise SystemExit(main())
