from __future__ import annotations

from sqlalchemy.orm import Session

from ..auth import Principal
from ..errors import InvalidTransitionError, PermissionDenied
from ..models import Applicant, ApplicantStatus
from ..workflow import transition

DECISION_TO_STATUS = {
    "approve": ApplicantStatus.APPROVED,
    "reject": ApplicantStatus.REJECTED,
    "needs_more_info": ApplicantStatus.NEEDS_MORE_INFO,
}


def claim(db: Session, applicant: Applicant, reviewer: Principal) -> Applicant:
    if applicant.status != ApplicantStatus.UNDER_REVIEW:
        raise InvalidTransitionError(
            f"Only applicants under review can be claimed (status '{applicant.status.value}')."
        )
    if applicant.assigned_reviewer and applicant.assigned_reviewer != reviewer.name:
        raise InvalidTransitionError(
            f"Already assigned to {applicant.assigned_reviewer}.",
            details={"assigned_reviewer": applicant.assigned_reviewer},
        )
    applicant.assigned_reviewer = reviewer.name
    db.commit()
    db.refresh(applicant)
    return applicant


def decide(
    db: Session, applicant: Applicant, *, decision: str, reason: str, reviewer: Principal
) -> Applicant:
    target = DECISION_TO_STATUS[decision]
    if decision != "approve" and not reason.strip():
        raise InvalidTransitionError("A reason is required when rejecting or requesting more info.")
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
    db.commit()
    db.refresh(applicant)
    return applicant
