from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, inspect, text

from kyc.config import get_settings
from kyc.database import Base, SessionLocal
from kyc.migrations import upgrade_schema
from kyc.models import Applicant
from kyc.services.review import release_expired_claims
from tests.conftest import AGENT, REVIEWER, submit_for_review, use_two_reviewers


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


def expire_claim(applicant_id: str) -> None:
    with SessionLocal() as db:
        row = db.get(Applicant, applicant_id)
        row.claimed_at = datetime.now(timezone.utc) - timedelta(minutes=241)
        db.commit()


def test_expired_claim_returns_to_the_queue_and_is_audited(client, create_applicant):
    applicant_id = submit_for_review(client, create_applicant)
    client.post(f"/v1/review/{applicant_id}/claim", headers=REVIEWER)
    assert client.get("/v1/review/queue?unassigned=true", headers=REVIEWER).json()["total"] == 0

    expire_claim(applicant_id)
    queue = client.get("/v1/review/queue?unassigned=true", headers=REVIEWER).json()
    assert [item["id"] for item in queue["items"]] == [applicant_id]
    assert queue["items"][0]["assigned_reviewer"] == ""
    assert queue["items"][0]["claimed_at"] is None

    events = client.get(f"/v1/applicants/{applicant_id}/assignments", headers=REVIEWER).json()
    assert [(e["action"], e["reviewer"]) for e in events] == [
        ("claimed", "Nadia"),
        ("expired", "Nadia"),
    ]
    # Expiry is not a state change, so the SLA clock and audit trail are untouched.
    history = client.get(f"/v1/applicants/{applicant_id}/history", headers=REVIEWER).json()
    assert history[-1]["to_status"] == "under_review"


def test_live_claim_is_not_released(client, create_applicant):
    applicant_id = submit_for_review(client, create_applicant)
    client.post(f"/v1/review/{applicant_id}/claim", headers=REVIEWER)
    with SessionLocal() as db:
        assert release_expired_claims(db, get_settings()) == 0
    body = client.get(f"/v1/applicants/{applicant_id}", headers=REVIEWER).json()
    assert body["assigned_reviewer"] == "Nadia"


def test_ttl_comes_from_settings(client, create_applicant, monkeypatch):
    monkeypatch.setenv("CLAIM_TTL_MINUTES", "1000")
    get_settings.cache_clear()
    applicant_id = submit_for_review(client, create_applicant)
    client.post(f"/v1/review/{applicant_id}/claim", headers=REVIEWER)
    expire_claim(applicant_id)  # 241 minutes old, well inside this TTL
    with SessionLocal() as db:
        assert release_expired_claims(db, get_settings()) == 0


def test_expired_claim_can_be_taken_by_another_reviewer(client, create_applicant, monkeypatch):
    second = use_two_reviewers(monkeypatch)
    applicant_id = submit_for_review(client, create_applicant)
    client.post(f"/v1/review/{applicant_id}/claim", headers=REVIEWER)
    assert client.post(f"/v1/review/{applicant_id}/claim", headers=second).status_code == 409

    expire_claim(applicant_id)
    taken = client.post(f"/v1/review/{applicant_id}/claim", headers=second)
    assert taken.status_code == 200 and taken.json()["assigned_reviewer"] == "Farhan"


def test_former_holder_cannot_decide_after_reassignment(client, create_applicant, monkeypatch):
    second = use_two_reviewers(monkeypatch)
    applicant_id = submit_for_review(client, create_applicant)
    client.post(f"/v1/review/{applicant_id}/claim", headers=REVIEWER)
    expire_claim(applicant_id)
    client.post(f"/v1/review/{applicant_id}/claim", headers=second)

    late = client.post(
        f"/v1/review/{applicant_id}/decision", json={"decision": "approve"}, headers=REVIEWER
    )
    assert late.status_code == 403


def test_expired_claim_does_not_block_a_decision(client, create_applicant, monkeypatch):
    second = use_two_reviewers(monkeypatch)
    applicant_id = submit_for_review(client, create_applicant)
    client.post(f"/v1/review/{applicant_id}/claim", headers=REVIEWER)
    expire_claim(applicant_id)
    decided = client.post(
        f"/v1/review/{applicant_id}/decision", json={"decision": "approve"}, headers=second
    )
    assert decided.status_code == 200 and decided.json()["assigned_reviewer"] == "Farhan"


def test_reclaiming_your_own_case_renews_it(client, create_applicant):
    applicant_id = submit_for_review(client, create_applicant)
    client.post(f"/v1/review/{applicant_id}/claim", headers=REVIEWER)
    with SessionLocal() as db:
        row = db.get(Applicant, applicant_id)
        row.claimed_at = datetime.now(timezone.utc) - timedelta(minutes=200)
        db.commit()

    renewed = client.post(f"/v1/review/{applicant_id}/claim", headers=REVIEWER).json()
    claimed_at = datetime.fromisoformat(renewed["claimed_at"].rstrip("Z"))
    assert datetime.now(timezone.utc) - claimed_at.replace(tzinfo=timezone.utc) < timedelta(
        minutes=1
    )
    events = client.get(f"/v1/applicants/{applicant_id}/assignments", headers=REVIEWER).json()
    assert [e["action"] for e in events] == ["claimed"]


def test_assignments_are_visible_to_reviewers_only(client, create_applicant):
    applicant_id = submit_for_review(client, create_applicant)
    assert (
        client.get(f"/v1/applicants/{applicant_id}/assignments", headers=AGENT).status_code == 403
    )
