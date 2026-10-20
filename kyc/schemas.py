from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import ApplicantStatus, DocumentKind, RiskLevel

BD_PHONE = re.compile(r"^(\+8801|01)[3-9]\d{8}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_phone(value: str) -> str:
    value = value.strip().replace(" ", "")
    if not BD_PHONE.match(value):
        raise ValueError("must be a Bangladeshi mobile number, e.g. 017XXXXXXXX")
    return value


def validate_email(value: str | None) -> str | None:
    if value is None or value == "":
        return value
    value = value.strip().lower()
    if not EMAIL.match(value):
        raise ValueError("must be a valid email address")
    return value


class ApplicantCreate(BaseModel):
    full_name: str = Field(min_length=3, max_length=160)
    nid_number: str = Field(min_length=10, max_length=20)
    date_of_birth: date
    phone: str
    email: str | None = None
    address: str = ""
    occupation: str = ""
    monthly_income_bdt: int | None = Field(default=None, ge=0)

    @field_validator("phone")
    @classmethod
    def _phone(cls, value: str) -> str:
        return validate_phone(value)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        return validate_email(value)

    @field_validator("nid_number")
    @classmethod
    def _nid(cls, value: str) -> str:
        return re.sub(r"[\s-]", "", value)


class ApplicantUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=3, max_length=160)
    nid_number: str | None = Field(default=None, min_length=10, max_length=20)
    date_of_birth: date | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    occupation: str | None = None
    monthly_income_bdt: int | None = Field(default=None, ge=0)

    @field_validator("phone")
    @classmethod
    def _phone(cls, value: str | None) -> str | None:
        return validate_phone(value) if value else value

    @field_validator("email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        return validate_email(value)

    @field_validator("nid_number")
    @classmethod
    def _nid(cls, value: str | None) -> str | None:
        return re.sub(r"[\s-]", "", value) if value else value


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    kind: DocumentKind
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    uploaded_at: datetime


class TransitionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    from_status: str | None
    to_status: str
    actor: str
    actor_role: str
    reason: str
    seconds_in_previous_state: float | None
    created_at: datetime


class CheckResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    run_id: str
    name: str
    passed: bool
    risk_points: int
    details: dict[str, Any]
    created_at: datetime


class ApplicantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    full_name: str
    nid_number: str
    date_of_birth: date
    phone: str
    email: str
    address: str
    occupation: str
    monthly_income_bdt: int | None
    status: ApplicantStatus
    status_entered_at: datetime
    risk_level: RiskLevel | None
    risk_score: int | None
    assigned_reviewer: str
    decision_reason: str
    missing_documents: list[DocumentKind]
    created_at: datetime
    submitted_at: datetime | None
    decided_at: datetime | None
    documents: list[DocumentOut]


class ApplicantSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    full_name: str
    phone: str
    status: ApplicantStatus
    status_entered_at: datetime
    risk_level: RiskLevel | None
    risk_score: int | None
    assigned_reviewer: str
    submitted_at: datetime | None


class Page(BaseModel):
    items: list[ApplicantSummary]
    total: int
    limit: int
    offset: int


class DecisionIn(BaseModel):
    decision: str = Field(pattern="^(approve|reject|needs_more_info)$")
    reason: str = Field(default="", max_length=1000)


class ChecksRunOut(BaseModel):
    applicant_id: str
    status: ApplicantStatus
    risk_score: int
    risk_level: RiskLevel
    results: list[CheckResultOut]


class StatusCount(BaseModel):
    status: ApplicantStatus
    count: int


class DailyStats(BaseModel):
    date: date
    created: int
    submitted: int
    approved: int
    rejected: int
    needs_more_info: int
    auto_approved: int
    by_risk_level: dict[str, int]
    avg_decision_hours: float | None
    open_by_status: list[StatusCount]


class SlaBucket(BaseModel):
    status: ApplicantStatus
    avg_hours: float | None
    max_hours: float | None
    samples: int


class SlaStats(BaseModel):
    review_sla_hours: int
    time_in_state: list[SlaBucket]
    breaching_now: int
    breaching_ids: list[str]


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any = None
