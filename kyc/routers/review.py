from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..auth import Principal, require_role
from ..config import Settings, get_settings
from ..database import get_db
from ..models import ApplicantStatus, RiskLevel
from ..schemas import (
    ApplicantOut,
    AvailabilityIn,
    DecisionIn,
    Page,
    ReviewerOut,
    ReviewerUpdate,
)
from ..services import applicants as svc
from ..services import review as review_svc
from ..services import reviewers as reviewer_svc

router = APIRouter(prefix="/v1/review", tags=["review"])


@router.get("/queue", response_model=Page)
def queue(
    risk_level: RiskLevel | None = None,
    mine: bool = False,
    unassigned: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_role("reviewer", "admin")),
    settings: Settings = Depends(get_settings),
):
    review_svc.release_expired_claims(db, settings)
    items, total = svc.list_applicants(
        db,
        status=ApplicantStatus.UNDER_REVIEW,
        risk_level=risk_level,
        reviewer=principal.name if mine else None,
        unassigned=unassigned,
        limit=limit,
        offset=offset,
    )
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/reviewers", response_model=list[ReviewerOut])
def reviewers(
    db: Session = Depends(get_db),
    _: Principal = Depends(require_role("reviewer", "admin")),
    settings: Settings = Depends(get_settings),
):
    review_svc.release_expired_claims(db, settings)
    return reviewer_svc.roster(db, settings)


@router.patch("/reviewers/me", response_model=ReviewerOut)
def set_my_availability(
    payload: AvailabilityIn,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_role("reviewer", "admin")),
    settings: Settings = Depends(get_settings),
):
    return reviewer_svc.update_reviewer(db, principal.name, settings, available=payload.available)


@router.patch("/reviewers/{name}", response_model=ReviewerOut)
def update_reviewer(
    name: str,
    payload: ReviewerUpdate,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_role("admin")),
    settings: Settings = Depends(get_settings),
):
    return reviewer_svc.update_reviewer(
        db, name, settings, **payload.model_dump(exclude_unset=True)
    )


@router.post("/{applicant_id}/claim", response_model=ApplicantOut)
def claim(
    applicant_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_role("reviewer", "admin")),
    settings: Settings = Depends(get_settings),
):
    return review_svc.claim(db, svc.get_applicant(db, applicant_id), principal, settings)


@router.post("/{applicant_id}/decision", response_model=ApplicantOut)
def decide(
    applicant_id: str,
    payload: DecisionIn,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_role("reviewer", "admin")),
    settings: Settings = Depends(get_settings),
):
    return review_svc.decide(
        db,
        svc.get_applicant(db, applicant_id),
        decision=payload.decision,
        reason=payload.reason,
        reviewer=principal,
        settings=settings,
    )
