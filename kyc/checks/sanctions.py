"""Sanctions / PEP screening against a local CSV list.

Names are normalised (case, punctuation, honorifics, token order) and compared with
``difflib`` similarity; a date-of-birth match raises confidence. The bundled
``sanctions_sample.csv`` is fictional sample data - point ``SANCTIONS_CSV`` at a real
consolidated list (UN, OFAC, local) in production.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from .base import CheckContext, CheckOutcome, register

_HONORIFICS = {"md", "mohammad", "mohammed", "muhammad", "mst", "mosammat", "mr", "mrs", "ms", "dr"}
NAME_THRESHOLD = 0.88


@dataclass(frozen=True)
class ListEntry:
    list_name: str
    full_name: str
    date_of_birth: date | None
    is_pep: bool
    reference: str


@dataclass(frozen=True)
class Match:
    entry: ListEntry
    name_similarity: float
    dob_match: bool


def normalize_name(name: str) -> str:
    tokens = re.sub(r"[^a-z\s]", " ", (name or "").lower()).split()
    tokens = [t for t in tokens if t not in _HONORIFICS]
    return " ".join(sorted(tokens))


def name_similarity(a: str, b: str) -> float:
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


@lru_cache(maxsize=4)
def load_list(path: Path) -> tuple[ListEntry, ...]:
    entries: list[ListEntry] = []
    with Path(path).open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            dob_raw = (row.get("date_of_birth") or "").strip()
            entries.append(
                ListEntry(
                    list_name=row.get("list_name", ""),
                    full_name=row.get("full_name", ""),
                    date_of_birth=date.fromisoformat(dob_raw) if dob_raw else None,
                    is_pep=(row.get("is_pep") or "").strip().lower() in {"true", "1", "yes"},
                    reference=row.get("reference", ""),
                )
            )
    return tuple(entries)


def screen(full_name: str, dob: date | None, entries: tuple[ListEntry, ...]) -> list[Match]:
    matches: list[Match] = []
    for entry in entries:
        sim = name_similarity(full_name, entry.full_name)
        dob_match = dob is not None and entry.date_of_birth == dob
        # A strong name match alone is a hit; a weaker name match needs the DOB to agree.
        if sim >= NAME_THRESHOLD or (sim >= 0.75 and dob_match):
            matches.append(Match(entry=entry, name_similarity=round(sim, 4), dob_match=dob_match))
    return sorted(matches, key=lambda m: (m.dob_match, m.name_similarity), reverse=True)


@register("sanctions_pep")
def check_sanctions(ctx: CheckContext) -> CheckOutcome:
    entries = load_list(ctx.settings.sanctions_path)
    matches = screen(ctx.applicant.full_name, ctx.applicant.date_of_birth, entries)
    if not matches:
        return CheckOutcome("sanctions_pep", passed=True, details={"matches": []})
    confirmed = any(m.dob_match for m in matches)
    pep_only = all(m.entry.is_pep for m in matches)
    points = 70 if confirmed and not pep_only else (45 if confirmed else 30)
    return CheckOutcome(
        name="sanctions_pep",
        passed=False,
        risk_points=points,
        details={
            "matches": [
                {
                    "list": m.entry.list_name,
                    "name": m.entry.full_name,
                    "reference": m.entry.reference,
                    "is_pep": m.entry.is_pep,
                    "name_similarity": m.name_similarity,
                    "dob_match": m.dob_match,
                }
                for m in matches
            ]
        },
    )
