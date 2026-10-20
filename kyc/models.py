from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class ApplicantStatus(str, enum.Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_MORE_INFO = "needs_more_info"


class RiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DocumentKind(str, enum.Enum):
    NID_FRONT = "nid_front"
    NID_BACK = "nid_back"
    SELFIE = "selfie"


REQUIRED_DOCUMENTS = (DocumentKind.NID_FRONT, DocumentKind.NID_BACK, DocumentKind.SELFIE)


class Applicant(Base):
    __tablename__ = "applicants"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    full_name: Mapped[str] = mapped_column(String(160))
    nid_number: Mapped[str] = mapped_column(String(20), index=True)
    date_of_birth: Mapped[date] = mapped_column(Date)
    phone: Mapped[str] = mapped_column(String(20), index=True)
    email: Mapped[str] = mapped_column(String(160), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    occupation: Mapped[str] = mapped_column(String(120), default="")
    monthly_income_bdt: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[ApplicantStatus] = mapped_column(
        Enum(ApplicantStatus, native_enum=False, length=20),
        default=ApplicantStatus.DRAFT,
        index=True,
    )
    status_entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    risk_level: Mapped[RiskLevel | None] = mapped_column(
        Enum(RiskLevel, native_enum=False, length=10), nullable=True
    )
    risk_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checks_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    assigned_reviewer: Mapped[str] = mapped_column(String(120), default="")
    decision_reason: Mapped[str] = mapped_column(Text, default="")

    created_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    documents: Mapped[list[Document]] = relationship(
        back_populates="applicant", cascade="all, delete-orphan", order_by="Document.uploaded_at"
    )
    transitions: Mapped[list[StateTransition]] = relationship(
        back_populates="applicant",
        cascade="all, delete-orphan",
        order_by="StateTransition.created_at",
    )
    check_results: Mapped[list[CheckResult]] = relationship(
        back_populates="applicant", cascade="all, delete-orphan", order_by="CheckResult.created_at"
    )

    def document_of(self, kind: DocumentKind) -> Document | None:
        for doc in reversed(self.documents):
            if doc.kind == kind:
                return doc
        return None

    @property
    def missing_documents(self) -> list[DocumentKind]:
        return [kind for kind in REQUIRED_DOCUMENTS if self.document_of(kind) is None]


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    applicant_id: Mapped[str] = mapped_column(
        ForeignKey("applicants.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[DocumentKind] = mapped_column(Enum(DocumentKind, native_enum=False, length=12))
    original_filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(80))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_key: Mapped[str] = mapped_column(String(255))
    uploaded_by: Mapped[str] = mapped_column(String(120), default="")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    applicant: Mapped[Applicant] = relationship(back_populates="documents")


class StateTransition(Base):
    """Audit trail: who moved an applicant from one state to another, and why."""

    __tablename__ = "state_transitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    applicant_id: Mapped[str] = mapped_column(
        ForeignKey("applicants.id", ondelete="CASCADE"), index=True
    )
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20))
    actor: Mapped[str] = mapped_column(String(120))
    actor_role: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text, default="")
    seconds_in_previous_state: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    applicant: Mapped[Applicant] = relationship(back_populates="transitions")


class CheckResult(Base):
    __tablename__ = "check_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    applicant_id: Mapped[str] = mapped_column(
        ForeignKey("applicants.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(60))
    passed: Mapped[bool] = mapped_column(Boolean)
    risk_points: Mapped[int] = mapped_column(Integer, default=0)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    applicant: Mapped[Applicant] = relationship(back_populates="check_results")
