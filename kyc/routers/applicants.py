from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..auth import Principal, require_role
from ..config import Settings, get_settings
from ..database import SessionLocal, get_db
from ..models import ApplicantStatus, DocumentKind, RiskLevel
from ..schemas import (
    ApplicantCreate,
    ApplicantOut,
    ApplicantUpdate,
    CheckResultOut,
    ChecksRunOut,
    DocumentOut,
    Page,
    TransitionOut,
)
from ..services import applicants as svc
from ..services import documents as doc_svc

router = APIRouter(prefix="/v1/applicants", tags=["applicants"])


def _process_in_background(applicant_id: str, settings: Settings) -> None:
    """Runs after the response is sent; owns its own session."""
    db = SessionLocal()
    try:
        svc.process_submission(db, applicant_id, settings)
    finally:
        db.close()


@router.post("", response_model=ApplicantOut, status_code=status.HTTP_201_CREATED)
def create_applicant(
    payload: ApplicantCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_role("agent", "admin")),
):
    return svc.create_applicant(db, payload, principal)


@router.get("", response_model=Page)
def list_applicants(
    status_: ApplicantStatus | None = Query(default=None, alias="status"),
    risk_level: RiskLevel | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: Principal = Depends(require_role("agent", "reviewer", "admin")),
):
    items, total = svc.list_applicants(
        db, status=status_, risk_level=risk_level, limit=limit, offset=offset
    )
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/{applicant_id}", response_model=ApplicantOut)
def get_applicant(
    applicant_id: str,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_role("agent", "reviewer", "admin")),
):
    return svc.get_applicant(db, applicant_id)


@router.patch("/{applicant_id}", response_model=ApplicantOut)
def update_applicant(
    applicant_id: str,
    payload: ApplicantUpdate,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_role("agent", "admin")),
):
    return svc.update_applicant(db, svc.get_applicant(db, applicant_id), payload)


@router.post(
    "/{applicant_id}/documents", response_model=DocumentOut, status_code=status.HTTP_201_CREATED
)
async def upload_document(
    applicant_id: str,
    kind: DocumentKind = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_role("agent", "admin")),
    settings: Settings = Depends(get_settings),
):
    applicant = svc.get_applicant(db, applicant_id)
    data = await file.read(settings.max_upload_bytes + 1)
    return doc_svc.store_document(
        db,
        applicant,
        kind=kind,
        data=data,
        filename=file.filename or "upload",
        declared_type=file.content_type or "",
        actor=principal,
        settings=settings,
    )


@router.get("/{applicant_id}/documents", response_model=list[DocumentOut])
def list_documents(
    applicant_id: str,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_role("agent", "reviewer", "admin")),
):
    return svc.get_applicant(db, applicant_id).documents


@router.get("/{applicant_id}/documents/{document_id}/content")
def download_document(
    applicant_id: str,
    document_id: str,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_role("reviewer", "admin")),
):
    document = doc_svc.get_document(db, document_id)
    if document.applicant_id != applicant_id:
        from ..errors import NotFoundError

        raise NotFoundError("Document does not belong to this applicant.")
    return Response(
        content=doc_svc.read_content(document),
        media_type=document.content_type,
        headers={"Content-Disposition": f'inline; filename="{document.original_filename}"'},
    )


@router.post(
    "/{applicant_id}/submit", response_model=ApplicantOut, status_code=status.HTTP_202_ACCEPTED
)
def submit_applicant(
    applicant_id: str,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_role("agent", "admin")),
    settings: Settings = Depends(get_settings),
):
    applicant = svc.submit(db, svc.get_applicant(db, applicant_id), principal)
    background.add_task(_process_in_background, applicant.id, settings)
    return applicant


@router.post("/{applicant_id}/checks/run", response_model=ChecksRunOut)
def run_checks_now(
    applicant_id: str,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_role("admin")),
    settings: Settings = Depends(get_settings),
):
    applicant = svc.get_applicant(db, applicant_id)
    if applicant.status == ApplicantStatus.SUBMITTED:
        applicant = svc.process_submission(db, applicant.id, settings)
    else:
        applicant = svc.rerun_checks(db, applicant, settings)
    latest_run = applicant.check_results[-1].run_id if applicant.check_results else ""
    results = [r for r in applicant.check_results if r.run_id == latest_run]
    return ChecksRunOut(
        applicant_id=applicant.id,
        status=applicant.status,
        risk_score=applicant.risk_score or 0,
        risk_level=applicant.risk_level,
        results=[CheckResultOut.model_validate(r) for r in results],
    )


@router.get("/{applicant_id}/checks", response_model=list[CheckResultOut])
def list_checks(
    applicant_id: str,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_role("reviewer", "admin")),
):
    return svc.get_applicant(db, applicant_id).check_results


@router.get("/{applicant_id}/history", response_model=list[TransitionOut])
def history(
    applicant_id: str,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_role("agent", "reviewer", "admin")),
):
    return svc.get_applicant(db, applicant_id).transitions
