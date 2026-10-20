"""Execute every registered check for an applicant and persist the results."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Applicant, CheckResult, DocumentKind, utcnow
from ..storage import get_storage
from . import dob, duplicates, face, nid, sanctions  # noqa: F401  (registers checks)
from .base import CheckContext, CheckOutcome, registered_checks
from .risk import RiskAssessment, assess

logger = logging.getLogger(__name__)


def load_documents(applicant: Applicant) -> dict[str, bytes]:
    storage = get_storage()
    docs: dict[str, bytes] = {}
    for kind in DocumentKind:
        doc = applicant.document_of(kind)
        if doc is not None:
            try:
                docs[kind.value] = storage.load(doc.storage_key)
            except FileNotFoundError:
                logger.warning("document %s missing from storage key=%s", doc.id, doc.storage_key)
    return docs


def run_checks(
    db: Session, applicant: Applicant, settings: Settings
) -> tuple[list[CheckOutcome], RiskAssessment]:
    ctx = CheckContext(
        applicant=applicant, settings=settings, db=db, documents=load_documents(applicant)
    )
    run_id = uuid.uuid4().hex
    outcomes: list[CheckOutcome] = []
    for name, check in registered_checks().items():
        try:
            outcome = check(ctx)
        except Exception as exc:  # a broken check must not block the pipeline
            logger.exception("check %s crashed for applicant %s", name, applicant.id)
            outcome = CheckOutcome(
                name=name,
                passed=False,
                risk_points=20,
                details={"error": type(exc).__name__, "message": str(exc)},
            )
        outcomes.append(outcome)
        db.add(
            CheckResult(
                applicant_id=applicant.id,
                run_id=run_id,
                name=outcome.name,
                passed=outcome.passed,
                risk_points=outcome.risk_points,
                details=outcome.details,
            )
        )
    assessment = assess(applicant, outcomes, settings)
    applicant.risk_score = assessment.score
    applicant.risk_level = assessment.level
    applicant.checks_completed_at = utcnow()
    logger.info(
        "checks complete applicant=%s score=%s level=%s failed=%s",
        applicant.id,
        assessment.score,
        assessment.level.value,
        [o.name for o in outcomes if not o.passed],
    )
    return outcomes, assessment
