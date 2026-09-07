"""Human-written report content, and the loader that refuses to fake it (R3).

`interpretations.yaml` is written by a person with psychology training. Nothing
in this package generates, paraphrases or falls back to a default for any of it.

THE WHOLE POINT OF THIS MODULE IS THAT IT RAISES.

R3 is a rule in a document; `InterpretationMissing` is the same rule with teeth.
A report template that asks for a string nobody has written gets an exception,
not an empty paragraph — so an unfinished YAML file cannot become a PDF with a
polite gap in it that a student reads as "the system had nothing to say about
me". Appendix B's "every interpretation string is human-written; no placeholder
survives" is checked by this function on every render, not by remembering.

The absence that IS allowed is a whole module: GET2 was not administered (R10),
so §14 says that page is "omitted entirely (not shown blank)". That is decided
by the caller looking at `get2["instrument"]`, never by a missing string here.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import yaml

CONTENT_DIR = Path(__file__).resolve().parent
INTERPRETATIONS_PATH = CONTENT_DIR / "interpretations.yaml"


class InterpretationMissing(RuntimeError):
    """A report asked for interpretive text nobody has written yet (R3).

    Raised rather than returning "" or a generated sentence. This exception
    reaching `jobs.last_error` and alerting is the intended failure: the work is
    a psychologist writing a paragraph, and a queue row is how that gets noticed.
    """


class Interpretations:
    """Read-only access to `interpretations.yaml`, by dotted key.

    Every getter raises on a key that is absent, blank, or whitespace. Blank is
    treated exactly like absent because the shipped file has every string
    present-and-empty on purpose — it is a form to fill in, and a form filled
    with spaces is not filled.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def text(self, *path: str) -> str:
        """One interpretive string, by path — `text("interests", "letters", "R")`.

        The error names the dotted key and the file, because the person who has
        to act on it is writing YAML, not reading Python.
        """
        node: Any = self._data
        for part in path:
            if not isinstance(node, dict) or part not in node:
                raise InterpretationMissing(
                    f"{'.'.join(path)} is not in {INTERPRETATIONS_PATH.name} — "
                    "a person with psychology training writes this (R3)"
                )
            node = node[part]

        if not isinstance(node, str) or not node.strip():
            raise InterpretationMissing(
                f"{'.'.join(path)} is empty in {INTERPRETATIONS_PATH.name} — "
                "a person with psychology training writes this (R3)"
            )
        return node.strip()

    def items(self, *path: str) -> list[str]:
        """A written list of strings — the "questions to bring" page (§14 p.12).

        Same contract as `text`: a list that is absent, empty, or holds a blank
        entry raises. An empty list is the failure this catches — a page headed
        "Questions to bring to your counsellor" with nothing under it is worse
        than no page, and it is exactly what an unfilled YAML sequence produces.
        """
        node: Any = self._data
        for part in path:
            if not isinstance(node, dict) or part not in node:
                raise InterpretationMissing(
                    f"{'.'.join(path)} is not in {INTERPRETATIONS_PATH.name} — "
                    "a person with psychology training writes this (R3)"
                )
            node = node[part]

        if not isinstance(node, list) or not node:
            raise InterpretationMissing(
                f"{'.'.join(path)} is empty in {INTERPRETATIONS_PATH.name} — "
                "a person with psychology training writes this (R3)"
            )
        for index, entry in enumerate(node):
            if not isinstance(entry, str) or not entry.strip():
                raise InterpretationMissing(
                    f"{'.'.join(path)}[{index}] is empty in {INTERPRETATIONS_PATH.name} (R3)"
                )
        return [entry.strip() for entry in node]

    def has(self, *path: str) -> bool:
        """Whether a string is written, without raising.

        For the ONE legitimate caller shape: a template deciding whether an
        optional section exists at all. Never use it to substitute a fallback for
        a missing required string — that is the hole this module exists to close.
        """
        try:
            self.text(*path)
        except InterpretationMissing:
            return False
        return True

    def missing_keys(self) -> list[str]:
        """Every dotted key whose string is still blank.

        What `scripts/check_interpretations.py` prints, and what the test asserts
        against the key list the templates actually ask for. Sorted so a diff
        between two runs is readable.
        """
        found: list[str] = []
        _walk(self._data, (), found)
        return sorted(found)


def _walk(node: Any, path: tuple[str, ...], out: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            _walk(value, (*path, str(key)), out)
    elif isinstance(node, list):
        # An empty sequence is itself a missing entry: `questions: []` is a page
        # heading with nothing under it, which `items()` refuses at render time.
        # Reported under its own key so the two agree about what is unfilled.
        if not node:
            out.append(".".join(path))
        for index, entry in enumerate(node):
            _walk(entry, (*path, f"[{index}]"), out)
    elif not isinstance(node, str) or not node.strip():
        out.append(".".join(path))


_cache: Interpretations | None = None
_lock = threading.Lock()


def load_interpretations(path: Path | None = None) -> Interpretations:
    """Parse `interpretations.yaml`, cached.

    Cached because a cohort render (M11) would otherwise re-read and re-parse the
    file once per student. `path` bypasses the cache entirely so a test can load
    a fixture without poisoning it for the next test.

    A missing FILE raises here rather than at first use: the engine image ships
    it as package data (pyproject.toml's `package-data`), so its absence means a
    broken build, and that should fail on the first render rather than
    intermittently on whichever page reads the first string.
    """
    if path is not None:
        return Interpretations(_read(path))

    global _cache
    with _lock:
        if _cache is None:
            _cache = Interpretations(_read(INTERPRETATIONS_PATH))
        return _cache


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise InterpretationMissing(
            f"{path} does not exist — the engine image ships it as package data"
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise InterpretationMissing(f"{path} must be a YAML mapping, got {type(data).__name__}")
    return data
