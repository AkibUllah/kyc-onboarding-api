from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..auth import Principal
from ..config import Settings
from ..errors import InvalidTransitionError, PermissionDenied
from ..models import Applicant, ApplicantStatus, AssignmentAction, AssignmentEvent, utcnow
from ..workflow import transition

logger = logging.getLogger(__name__)

DECISION_TO_STATUS = {
    "approve": ApplicantStatus.APPROVED,
    "reject": ApplicantStatus.REJECTED,
    "needs_more_info": ApplicantStatus.NEEDS_MORE_INFO,
}


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
    db.refresh(applicant, with_for_update=True)
    if applicant.status != ApplicantStatus.UNDER_REVIEW:
        raise InvalidTransitionError(
            f"Only applicants under review can be claimed (status '{applicant.status.value}')."
        )
    if applicant.assigned_reviewer and applicant.assigned_reviewer != reviewer.name:
        raise InvalidTransitionError(
            f"Already assigned to {applicant.assigned_reviewer}.",
            details={"assigned_reviewer": applicant.assigned_reviewer},
        )
    if not applicant.assigned_reviewer:
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
    if (
        applicant.assigned_reviewer
        and applicant.assigned_reviewer not in {reviewer.name, "system"}
        and reviewer.role != "admin"
    ):
        raise PermissionDenied(
            f"Applicant is assigned to {applicant.assigned_reviewer}.",
            details={"assigned_reviewer": applicant.assigned_reviewer},
        )
    db.add(
        transition(applicant, target, actor=reviewer.name, actor_role=reviewer.role, reason=reason)
    )
    applicant.assigned_reviewer = reviewer.name
    applicant.claimed_at = None
    db.commit()
    db.refresh(applicant)
    return applicant
