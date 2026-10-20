"""Detect documents or identifiers already used by another applicant."""

from __future__ import annotations

from sqlalchemy import select

from ..models import Applicant, ApplicantStatus, Document
from .base import CheckContext, CheckOutcome, register


@register("duplicate_identity")
def check_duplicates(ctx: CheckContext) -> CheckOutcome:
    applicant = ctx.applicant
    db = ctx.db
    hashes = [doc.sha256 for doc in applicant.documents]
    reused_docs = []
    if hashes:
        rows = db.execute(
            select(Document.sha256, Document.applicant_id, Document.kind).where(
                Document.sha256.in_(hashes), Document.applicant_id != applicant.id
            )
        ).all()
        reused_docs = [
            {"sha256": r[0][:12], "applicant_id": r[1], "kind": r[2].value} for r in rows
        ]

    same_nid = db.execute(
        select(Applicant.id, Applicant.status).where(
            Applicant.nid_number == applicant.nid_number, Applicant.id != applicant.id
        )
    ).all()
    approved_elsewhere = [r[0] for r in same_nid if r[1] == ApplicantStatus.APPROVED]

    problems = []
    if reused_docs:
        problems.append("document_reused")
    if approved_elsewhere:
        problems.append("nid_already_approved")
    elif same_nid:
        problems.append("nid_in_other_application")
    points = 0
    if "document_reused" in problems:
        points += 40
    if "nid_already_approved" in problems:
        points += 50
    elif "nid_in_other_application" in problems:
        points += 15
    return CheckOutcome(
        name="duplicate_identity",
        passed=not problems,
        risk_points=min(points, 90),
        details={
            "problems": problems,
            "reused_documents": reused_docs,
            "other_applications_with_nid": [r[0] for r in same_nid],
        },
    )
