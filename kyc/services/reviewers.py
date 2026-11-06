"""Reviewer availability and open-case limits."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import Settings
from ..errors import NotFoundError, ReviewerAtCapacity, ReviewerUnavailable
from ..models import Applicant, ApplicantStatus, ReviewerProfile, utcnow
from ..schemas import ReviewerOut

REVIEW_ROLES = ("reviewer", "admin")


def reviewer_names(settings: Settings) -> list[str]:
    return sorted(
        {key.name for key in settings.parsed_api_keys.values() if key.role in REVIEW_ROLES}
    )


def _ensure_profile(db: Session, name: str) -> None:
    if db.get(ReviewerProfile, name) is not None:
        return
    db.add(ReviewerProfile(name=name))
    try:
        db.commit()
    except IntegrityError:  # another request created it first
        db.rollback()


def reserve(db: Session, name: str, settings: Settings, now: datetime | None = None) -> int:
    """Lock the reviewer for this transaction and return their open-case limit.

    Starting with a write serialises assignments per reviewer on PostgreSQL (row lock) and
    overall on SQLite (database write lock), so counting open cases and then assigning one
    cannot overshoot the limit, and availability cannot change until the assignment commits.
    """
    _ensure_profile(db, name)
    row = db.execute(
        update(ReviewerProfile)
        .where(ReviewerProfile.name == name, ReviewerProfile.available.is_(True))
        .values(updated_at=now or utcnow())
        .returning(ReviewerProfile.max_open_cases)
        .execution_options(synchronize_session=False)
    ).first()
    if row is None:
        raise ReviewerUnavailable(f"{name} is not taking new cases.")
    return settings.max_open_cases if row.max_open_cases is None else row.max_open_cases


def open_cases(db: Session, name: str) -> int:
    return db.execute(
        select(func.count())
        .select_from(Applicant)
        .where(
            Applicant.status == ApplicantStatus.UNDER_REVIEW,
            Applicant.assigned_reviewer == name,
        )
    ).scalar_one()


def require_capacity(db: Session, name: str, limit: int) -> None:
    current = open_cases(db, name)
    if current >= limit:
        raise ReviewerAtCapacity(
            f"{name} already holds {current} open cases.",
            details={"open_cases": current, "max_open_cases": limit},
        )


def is_available(db: Session, name: str) -> bool:
    available = db.execute(
        select(ReviewerProfile.available).where(ReviewerProfile.name == name)
    ).scalar_one_or_none()
    return available is not False


def roster(db: Session, settings: Settings) -> list[ReviewerOut]:
    """Every reviewer with an API key, with their current load."""
    profiles = {p.name: p for p in db.execute(select(ReviewerProfile)).scalars()}
    counts = dict(
        db.execute(
            select(Applicant.assigned_reviewer, func.count())
            .where(Applicant.status == ApplicantStatus.UNDER_REVIEW)
            .group_by(Applicant.assigned_reviewer)
        ).all()
    )
    return [
        _describe(name, profiles.get(name), counts.get(name, 0), settings)
        for name in reviewer_names(settings)
    ]


def update_reviewer(db: Session, name: str, settings: Settings, **changes: Any) -> ReviewerOut:
    if name not in reviewer_names(settings):
        raise NotFoundError(f"Reviewer '{name}' not found.")
    _ensure_profile(db, name)
    profile = db.execute(
        select(ReviewerProfile)
        .where(ReviewerProfile.name == name)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    if changes.get("available") is not None:
        profile.available = changes["available"]
    if "max_open_cases" in changes:
        profile.max_open_cases = changes["max_open_cases"]
    db.commit()
    return _describe(name, profile, open_cases(db, name), settings)


def _describe(
    name: str, profile: ReviewerProfile | None, current: int, settings: Settings
) -> ReviewerOut:
    limit = profile.max_open_cases if profile else None
    return ReviewerOut(
        name=name,
        available=profile.available if profile else True,
        max_open_cases=settings.max_open_cases if limit is None else limit,
        open_cases=current,
    )
