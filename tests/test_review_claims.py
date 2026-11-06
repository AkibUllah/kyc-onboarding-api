from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from kyc.database import Base, SessionLocal
from kyc.migrations import upgrade_schema
from kyc.models import Applicant
from tests.conftest import AGENT, REVIEWER, upload_all


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


def test_claim_is_timestamped_and_cleared_by_the_decision(client, create_applicant):
    applicant_id = submit_for_review(client, create_applicant)
    queued = client.get(f"/v1/applicants/{applicant_id}", headers=REVIEWER).json()
    assert queued["claimed_at"] is None

    claimed = client.post(f"/v1/review/{applicant_id}/claim", headers=REVIEWER).json()
    assert claimed["claimed_at"] is not None

    decided = client.post(
        f"/v1/review/{applicant_id}/decision",
        json={"decision": "needs_more_info", "reason": "Blurred selfie"},
        headers=REVIEWER,
    ).json()
    assert decided["claimed_at"] is None
    assert decided["assigned_reviewer"] == "Nadia"


def test_resubmitted_applicant_returns_unassigned(client, create_applicant):
    applicant_id = submit_for_review(client, create_applicant)
    client.post(f"/v1/review/{applicant_id}/claim", headers=REVIEWER)
    client.post(
        f"/v1/review/{applicant_id}/decision",
        json={"decision": "needs_more_info", "reason": "Blurred selfie"},
        headers=REVIEWER,
    )
    client.post(f"/v1/applicants/{applicant_id}/submit", headers=AGENT)
    body = client.get(f"/v1/applicants/{applicant_id}", headers=REVIEWER).json()
    assert body["status"] == "under_review"
    assert body["assigned_reviewer"] == "" and body["claimed_at"] is None


def test_upgrade_adds_claimed_at_and_stamps_open_claims(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX ix_applicants_claimed_at"))
        connection.execute(text("ALTER TABLE applicants DROP COLUMN claimed_at"))
        for row_id, status, reviewer in (
            ("held", "UNDER_REVIEW", "Nadia"),
            ("free", "UNDER_REVIEW", ""),
            ("done", "APPROVED", "Nadia"),
        ):
            connection.execute(
                text(
                    "INSERT INTO applicants (id, full_name, nid_number, date_of_birth, phone, "
                    "email, address, occupation, status, status_entered_at, assigned_reviewer, "
                    "decision_reason, created_by, created_at, updated_at) VALUES "
                    "(:id, 'A B', '1990123456789', '1990-01-01', '01711223344', '', '', '', "
                    ":status, '2026-01-01', :reviewer, '', '', '2026-01-01', '2026-01-01')"
                ),
                {"id": row_id, "status": status, "reviewer": reviewer},
            )

    upgrade_schema(engine)
    upgrade_schema(engine)  # second start is a no-op

    inspector = inspect(engine)
    assert "claimed_at" in {c["name"] for c in inspector.get_columns("applicants")}
    assert "ix_applicants_claimed_at" in {i["name"] for i in inspector.get_indexes("applicants")}
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT id, claimed_at FROM applicants")).all()
    stamped = {row_id for row_id, claimed_at in rows if claimed_at is not None}
    assert stamped == {"held"}
    engine.dispose()


def test_upgrade_is_a_no_op_on_a_current_schema():
    upgrade_schema(SessionLocal.kw["bind"])
    with SessionLocal() as db:
        assert db.query(Applicant).count() == 0
