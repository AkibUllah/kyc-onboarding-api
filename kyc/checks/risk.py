"""Weighted risk scoring.

Each failed check contributes its ``risk_points``; declared profile factors add a
little more (high stated income relative to occupation is a common MFS red flag,
missing contact details another). The total maps to LOW / MEDIUM / HIGH using the
configurable thresholds, and LOW can be auto-approved.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from ..config import Settings
from ..models import Applicant, RiskLevel
from .base import CheckOutcome


@dataclass(frozen=True)
class RiskAssessment:
    score: int
    level: RiskLevel
    factors: list[str] = field(default_factory=list)


def profile_factors(applicant: Applicant) -> list[tuple[str, int]]:
    factors: list[tuple[str, int]] = []
    if not applicant.email:
        factors.append(("no_email", 5))
    if not applicant.address.strip():
        factors.append(("no_address", 10))
    if applicant.monthly_income_bdt is not None and applicant.monthly_income_bdt >= 500_000:
        factors.append(("high_declared_income", 10))
    if not applicant.occupation.strip():
        factors.append(("no_occupation", 5))
    return factors


def score_to_level(score: int, settings: Settings) -> RiskLevel:
    if score >= settings.risk_high_threshold:
        return RiskLevel.HIGH
    if score >= settings.risk_medium_threshold:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def assess(
    applicant: Applicant, outcomes: Iterable[CheckOutcome], settings: Settings
) -> RiskAssessment:
    score = 0
    factors: list[str] = []
    for outcome in outcomes:
        if not outcome.passed:
            score += outcome.risk_points
            factors.append(f"check:{outcome.name}")
    for name, points in profile_factors(applicant):
        score += points
        factors.append(f"profile:{name}")
    score = min(score, 100)
    return RiskAssessment(score=score, level=score_to_level(score, settings), factors=factors)
