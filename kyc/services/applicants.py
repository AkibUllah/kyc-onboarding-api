from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..auth import Principal
from ..checks.runner import run_checks
from ..config import Settings
from ..errors import InvalidTransitionError, NotFoundError, ValidationFailed
from ..models import Applicant, ApplicantStatus, RiskLevel
from ..schemas import ApplicantCreate, ApplicantUpdate
from ..workflow import EDITABLE_STATES, transition

logger = logging.getLogger(__name__)

SYSTEM_ACTOR = "system"


def get_applicant(db: Session, applicant_id: str) -> Applicant:
    applicant = db.execute(
        select(Applicant)
        .options(selectinload(Applicant.documents))
        .where(Applicant.id == applicant_id)
    ).scalar_one_or_none()
    if applicant is None:
        raise NotFoundError(f"Applicant '{applicant_id}' not found.")
    return applicant


def create_applicant(db: Session, data: ApplicantCreate, actor: Principal) -> Applicant:
    applicant = Applicant(
        **data.model_dump(exclude={"email"}), email=data.email or "", created_by=actor.name
    )
    db.add(applicant)
    db.flush()
    db.add(_initial_transition(applicant, actor))
    db.commit()
    db.refresh(applicant)
    return applicant


def _initial_transition(applicant: Applicant, actor: Principal):
    from ..models import StateTransition

    return StateTransition(
        applicant_id=applicant.id,
        from_status=None,
        to_status=ApplicantStatus.DRAFT.value,
        actor=actor.name,
        actor_role=actor.role,
        reason="Application created",
    )


def update_applicant(db: Session, applicant: Applicant, data: ApplicantUpdate) -> Applicant:
    if applicant.status not in EDITABLE_STATES:
        raise InvalidTransitionError(
            f"Applicant in status '{applicant.status.value}' cannot be edited.",
            details={"editable_statuses": sorted(s.value for s in EDITABLE_STATES)},
        )
    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(
            applicant, field, value if value is not None or field == "monthly_income_bdt" else ""
        )
    db.commit()
    db.refresh(applicant)
    return applicant


def submit(db: Session, applicant: Applicant, actor: Principal) -> Applicant:
    """DRAFT / NEEDS_MORE_INFO -> SUBMITTED. Checks run afterwards (background or inline)."""
    missing = applicant.missing_documents
    if missing:
        raise ValidationFailed(
            "All required documents must be uploaded before submission.",
            details={"missing_documents": [m.value for m in missing]},
        )
    db.add(
        transition(
            applicant,
            ApplicantStatus.SUBMITTED,
            actor=actor.name,
            actor_role=actor.role,
            reason="Submitted for verification",
        )
    )
    db.commit()
    db.refresh(applicant)
    return applicant


def process_submission(db: Session, applicant_id: str, settings: Settings) -> Applicant:
    """Run verification checks and move SUBMITTED -> UNDER_REVIEW (-> APPROVED if low risk)."""
    applicant = get_applicant(db, applicant_id)
    if applicant.status != ApplicantStatus.SUBMITTED:
        logger.info("skip processing applicant=%s status=%s", applicant.id, applicant.status.value)
        return applicant

    _, assessment = run_checks(db, applicant, settings)
    db.add(
        transition(
            applicant,
            ApplicantStatus.UNDER_REVIEW,
            actor=SYSTEM_ACTOR,
            actor_role="system",
            reason=f"Checks complete: risk {assessment.level.value} ({assessment.score})",
        )
    )
    if settings.auto_approve_low_risk and assessment.level == RiskLevel.LOW:
        db.add(
            transition(
                applicant,
                ApplicantStatus.APPROVED,
                actor=SYSTEM_ACTOR,
                actor_role="system",
                reason="Auto-approved: low risk, all checks passed",
            )
        )
        applicant.assigned_reviewer = SYSTEM_ACTOR
    db.commit()
    db.refresh(applicant)
    return applicant


def rerun_checks(db: Session, applicant: Applicant, settings: Settings) -> Applicant:
    """Admin re-run on an applicant already under review (e.g. after a list update)."""
    if applicant.status in {
        ApplicantStatus.DRAFT,
        ApplicantStatus.APPROVED,
        ApplicantStatus.REJECTED,
    }:
        raise InvalidTransitionError(
            f"Checks cannot be re-run while applicant is '{applicant.status.value}'."
        )
    run_checks(db, applicant, settings)
    db.commit()
    db.refresh(applicant)
    return applicant


def list_applicants(
    db: Session,
    *,
    status: ApplicantStatus | None = None,
    risk_level: RiskLevel | None = None,
    reviewer: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Applicant], int]:
    query = select(Applicant)
    if status is not None:
        query = query.where(Applicant.status == status)
    if risk_level is not None:
        query = query.where(Applicant.risk_level == risk_level)
    if reviewer:
        query = query.where(Applicant.assigned_reviewer == reviewer)
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    rows = (
        db.execute(
            query.order_by(Applicant.submitted_at.asc().nulls_last(), Applicant.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return list(rows), total
