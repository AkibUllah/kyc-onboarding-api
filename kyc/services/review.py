from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, Select, case, exists, or_, select, update
from sqlalchemy.orm import Session

from ..auth import Principal
from ..config import Settings
from ..errors import InvalidTransitionError, PermissionDenied, ReviewerUnavailable
from ..models import (
    Applicant,
    ApplicantStatus,
    AssignmentAction,
    AssignmentEvent,
    RiskLevel,
    StateTransition,
    utcnow,
)
from ..workflow import sla_deadline, transition
from . import reviewers
from .applicants import get_applicant

logger = logging.getLogger(__name__)

DECISION_TO_STATUS = {
    "approve": ApplicantStatus.APPROVED,
    "reject": ApplicantStatus.REJECTED,
    "needs_more_info": ApplicantStatus.NEEDS_MORE_INFO,
}


def _dispatch_order(settings: Settings, now: datetime) -> tuple[ColumnElement, ...]:
    """Order in which ``POST /v1/review/next`` hands out work.

    Cases past the review SLA come first, most overdue first, whatever their risk: otherwise
    a steady stream of high-risk cases would starve the older ones. Everything still inside
    the SLA goes by risk, then longest wait. The clock is ``status_entered_at``, the same one
    ``/v1/stats/sla`` reports on; an expired claim does not reset it.
    """
    overdue = Applicant.status_entered_at <= sla_deadline(settings.review_sla_hours, now)
    risk = case(
        (Applicant.risk_level == RiskLevel.HIGH, 0),
        (Applicant.risk_level == RiskLevel.MEDIUM, 1),
        (Applicant.risk_level == RiskLevel.LOW, 2),
        else_=3,
    )
    return (
        case((overdue, 0), else_=1),
        case((overdue, 0), else_=risk),
        Applicant.status_entered_at.asc(),
        Applicant.id.asc(),
    )


def _submitted_by(name: str) -> ColumnElement[bool]:
    """Whether ``name`` created the applicant or ever submitted it, across re-submissions."""
    return or_(
        Applicant.created_by == name,
        exists().where(
            StateTransition.applicant_id == Applicant.id,
            StateTransition.to_status == ApplicantStatus.SUBMITTED.value,
            StateTransition.actor == name,
        ),
    )


def _require_independent_reviewer(db: Session, applicant: Applicant, reviewer: Principal) -> None:
    submitted = db.execute(
        select(Applicant.id).where(Applicant.id == applicant.id, _submitted_by(reviewer.name))
    ).first()
    if submitted is not None:
        raise PermissionDenied("Reviewers may not handle an application they submitted.")


def release_expired_claims(db: Session, settings: Settings, now: datetime | None = None) -> int:
    """Return claims older than ``CLAIM_TTL_MINUTES`` to the queue and commit.

    Runs at the start of every review operation, so nothing observes a stale claim through
    the review API and no background worker is needed. Replicas sweeping at the same time
    skip each other's rows; the conditional update covers databases without row locks.
    """
    now = now or utcnow()
    cutoff = now - timedelta(minutes=settings.claim_ttl_minutes)
    expired = db.execute(
        select(Applicant.id, Applicant.assigned_reviewer)
        .where(
            Applicant.status == ApplicantStatus.UNDER_REVIEW,
            Applicant.assigned_reviewer != "",
            Applicant.claimed_at <= cutoff,
        )
        .with_for_update(skip_locked=True)
    ).all()
    released = 0
    for applicant_id, holder in expired:
        result = db.execute(
            update(Applicant)
            .where(
                Applicant.id == applicant_id,
                Applicant.assigned_reviewer == holder,
                Applicant.claimed_at <= cutoff,
            )
            .values(assigned_reviewer="", claimed_at=None, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount:
            released += 1
            db.add(
                AssignmentEvent(
                    applicant_id=applicant_id,
                    action=AssignmentAction.EXPIRED,
                    reviewer=holder,
                    created_at=now,
                )
            )
    db.commit()
    if released:
        logger.info("returned %s expired claims to the queue", released)
    return released


def claim(db: Session, applicant: Applicant, reviewer: Principal, settings: Settings) -> Applicant:
    release_expired_claims(db, settings)
    limit = reviewers.reserve(db, reviewer.name, settings)
    db.refresh(applicant, with_for_update=True)
    if applicant.status != ApplicantStatus.UNDER_REVIEW:
        raise InvalidTransitionError(
            f"Only applicants under review can be claimed (status '{applicant.status.value}')."
        )
    _require_independent_reviewer(db, applicant, reviewer)
    if applicant.assigned_reviewer and applicant.assigned_reviewer != reviewer.name:
        raise InvalidTransitionError(
            f"Already assigned to {applicant.assigned_reviewer}.",
            details={"assigned_reviewer": applicant.assigned_reviewer},
        )
    if not applicant.assigned_reviewer:
        reviewers.require_capacity(db, reviewer.name, limit)
        db.add(
            AssignmentEvent(
                applicant_id=applicant.id,
                action=AssignmentAction.CLAIMED,
                reviewer=reviewer.name,
            )
        )
    # Claiming a case you already hold renews it.
    applicant.assigned_reviewer = reviewer.name
    applicant.claimed_at = utcnow()
    db.commit()
    db.refresh(applicant)
    return applicant


def _next_candidate(name: str, settings: Settings, now: datetime) -> Select[tuple[str]]:
    return (
        select(Applicant.id)
        .where(
            Applicant.status == ApplicantStatus.UNDER_REVIEW,
            Applicant.assigned_reviewer == "",
            ~_submitted_by(name),
        )
        .order_by(*_dispatch_order(settings, now))
        .limit(1)
        .with_for_update(skip_locked=True)
    )


def assign_next(
    db: Session, reviewer: Principal, settings: Settings, now: datetime | None = None
) -> Applicant | None:
    """Give the reviewer the next case in dispatch order, or ``None`` if nothing is eligible.

    Selection and assignment are one ``UPDATE``, so a case can only go to one reviewer. On
    PostgreSQL ``SKIP LOCKED`` also lets concurrent reviewers pass over each other's rows
    rather than queue behind them; SQLite ignores it and runs the statement atomically.
    """
    now = now or utcnow()
    release_expired_claims(db, settings, now)
    limit = reviewers.reserve(db, reviewer.name, settings, now)
    reviewers.require_capacity(db, reviewer.name, limit)
    applicant_id = db.execute(
        update(Applicant)
        .where(Applicant.id == _next_candidate(reviewer.name, settings, now).scalar_subquery())
        .values(assigned_reviewer=reviewer.name, claimed_at=now, updated_at=now)
        .returning(Applicant.id)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    if applicant_id is None:
        db.commit()
        return None
    db.add(
        AssignmentEvent(
            applicant_id=applicant_id,
            action=AssignmentAction.AUTO_ASSIGNED,
            reviewer=reviewer.name,
            created_at=now,
        )
    )
    db.commit()
    return get_applicant(db, applicant_id)


def decide(
    db: Session,
    applicant: Applicant,
    *,
    decision: str,
    reason: str,
    reviewer: Principal,
    settings: Settings,
) -> Applicant:
    target = DECISION_TO_STATUS[decision]
    if decision != "approve" and not reason.strip():
        raise InvalidTransitionError("A reason is required when rejecting or requesting more info.")
    release_expired_claims(db, settings)
    db.refresh(applicant, with_for_update=True)
    _require_independent_reviewer(db, applicant, reviewer)
    holder = applicant.assigned_reviewer
    if holder and holder not in {reviewer.name, "system"} and reviewer.role != "admin":
        raise PermissionDenied(
            f"Applicant is assigned to {holder}.", details={"assigned_reviewer": holder}
        )
    if holder != reviewer.name and not reviewers.is_available(db, reviewer.name):
        raise ReviewerUnavailable(f"{reviewer.name} is not taking new cases.")
    db.add(
        transition(applicant, target, actor=reviewer.name, actor_role=reviewer.role, reason=reason)
    )
    applicant.assigned_reviewer = reviewer.name
    applicant.claimed_at = None
    db.commit()
    db.refresh(applicant)
    return applicant
