from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import pytest

# Configure before the app modules are imported: in-memory SQLite unless TEST_DATABASE_URL points
# at a throwaway PostgreSQL (its tables are dropped between tests), local storage in tmp.
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "sqlite://")
os.environ["AUTO_APPROVE_LOW_RISK"] = "true"
os.environ["API_KEYS"] = (
    "agent-key:agent:Branch App,reviewer-key:reviewer:Nadia,admin-key:admin:Ops"
)

from fastapi.testclient import TestClient  # noqa: E402

from kyc.config import get_settings  # noqa: E402
from kyc.database import Base, SessionLocal, engine  # noqa: E402
from kyc.main import app  # noqa: E402
from kyc.models import Applicant, ApplicantStatus, RiskLevel  # noqa: E402
from kyc.storage import LocalStorage, set_storage  # noqa: E402

AGENT = {"X-API-Key": "agent-key"}
REVIEWER = {"X-API-Key": "reviewer-key"}
ADMIN = {"X-API-Key": "admin-key"}

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4\n" + b"\x00" * 64


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    get_settings.cache_clear()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    set_storage(LocalStorage(tmp_path / "storage"))
    yield
    set_storage(None)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def applicant_payload(**overrides) -> dict:
    base = {
        "full_name": "Rahim Uddin Ahmed",
        "nid_number": "1990123456789",  # 13-digit legacy NID
        "date_of_birth": "1990-05-14",
        "phone": "01711223344",
        "email": "rahim@example.com",
        "address": "House 12, Road 5, Dhanmondi, Dhaka",
        "occupation": "Shop owner",
        "monthly_income_bdt": 45000,
    }
    base.update(overrides)
    return base


def upload(
    client,
    applicant_id: str,
    kind: str,
    data: bytes,
    content_type: str = "image/jpeg",
    filename: str = "doc.jpg",
    headers=AGENT,
):
    return client.post(
        f"/v1/applicants/{applicant_id}/documents",
        data={"kind": kind},
        files={"file": (filename, data, content_type)},
        headers=headers,
    )


def upload_all(
    client,
    applicant_id: str,
    selfie: bytes = PNG,
    nid_front: bytes = JPEG,
    nid_back: bytes = JPEG + b"back",
):
    assert upload(client, applicant_id, "nid_front", nid_front).status_code == 201
    assert upload(client, applicant_id, "nid_back", nid_back).status_code == 201
    selfie_type = "image/png" if selfie.startswith(b"\x89PNG") else "image/jpeg"
    assert (
        upload(client, applicant_id, "selfie", selfie, selfie_type, "selfie.img").status_code == 201
    )


def submit_for_review(client, create_applicant, **overrides) -> str:
    """Create an applicant that lands in the reviewer queue at high risk."""
    fields = {
        "full_name": "Tanvir Ahmed Siddique",
        "date_of_birth": "1983-05-30",
        "nid_number": "1983123456789",
        **overrides,
    }
    applicant = create_applicant(**fields)
    upload_all(client, applicant["id"])
    assert client.post(f"/v1/applicants/{applicant['id']}/submit", headers=AGENT).status_code == 202
    return applicant["id"]


def use_two_reviewers(monkeypatch) -> dict[str, str]:
    monkeypatch.setenv(
        "API_KEYS",
        "agent-key:agent:Branch App,reviewer-key:reviewer:Nadia,"
        "reviewer2-key:reviewer:Farhan,admin-key:admin:Ops",
    )
    get_settings.cache_clear()
    return {"X-API-Key": "reviewer2-key"}


def queued(
    applicant_id: str,
    *,
    hours_waiting: float = 1,
    risk: RiskLevel = RiskLevel.MEDIUM,
    created_by: str = "Branch App",
    session_factory=None,
) -> str:
    """Put an applicant straight in the queue so a test can control its age and risk."""
    entered = datetime.now(timezone.utc) - timedelta(hours=hours_waiting)
    with (session_factory or SessionLocal)() as db:
        db.add(
            Applicant(
                id=applicant_id,
                full_name="Queue Fixture",
                nid_number="1990123456789",
                date_of_birth=date(1990, 1, 1),
                phone="01711223344",
                status=ApplicantStatus.UNDER_REVIEW,
                status_entered_at=entered,
                submitted_at=entered,
                risk_level=risk,
                risk_score=50,
                created_by=created_by,
            )
        )
        db.commit()
    return applicant_id


@pytest.fixture
def create_applicant(client):
    def _create(**overrides):
        response = client.post("/v1/applicants", json=applicant_payload(**overrides), headers=AGENT)
        assert response.status_code == 201, response.text
        return response.json()

    return _create


@pytest.fixture
def adult_dob() -> date:
    return date(1990, 5, 14)
