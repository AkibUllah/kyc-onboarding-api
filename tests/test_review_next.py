from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.dialects import postgresql

from kyc.config import get_settings
from kyc.database import SessionLocal
from kyc.models import Applicant, ApplicantStatus, RiskLevel, StateTransition
from kyc.services import review
from tests.conftest import ADMIN, AGENT, REVIEWER, submit_for_review, use_two_reviewers


def queued(
    applicant_id: str,
    *,
    hours_waiting: float = 1,
    risk: RiskLevel = RiskLevel.MEDIUM,
    created_by: str = "Branch App",
) -> str:
    """Put an applicant straight in the queue so a test can control its age and risk."""
    entered = datetime.now(timezone.utc) - timedelta(hours=hours_waiting)
    with SessionLocal() as db:
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


def take(client, headers=REVIEWER) -> str | None:
    response = client.post("/v1/review/next", headers=headers)
    if response.status_code == 204:
        return None
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_assigns_to_the_caller_and_records_it(client, create_applicant):
    applicant_id = submit_for_review(client, create_applicant)
    body = client.post("/v1/review/next", headers=REVIEWER).json()
    assert body["id"] == applicant_id
    assert body["assigned_reviewer"] == "Nadia" and body["claimed_at"] is not None
    assert len(body["documents"]) == 3

    events = client.get(f"/v1/applicants/{applicant_id}/assignments", headers=REVIEWER).json()
    assert [(e["action"], e["reviewer"]) for e in events] == [("auto_assigned", "Nadia")]
    mine = client.get("/v1/review/queue?mine=true", headers=REVIEWER).json()
    assert [item["id"] for item in mine["items"]] == [applicant_id]


def test_empty_queue_is_204(client):
    response = client.post("/v1/review/next", headers=REVIEWER)
    assert response.status_code == 204 and response.content == b""


def test_requires_a_reviewer_role(client):
    assert client.post("/v1/review/next", headers=AGENT).status_code == 403
    assert client.post("/v1/review/next").status_code == 403


def test_higher_risk_goes_first_inside_the_sla(client):
    queued("low", hours_waiting=10, risk=RiskLevel.LOW)
    queued("medium", hours_waiting=8, risk=RiskLevel.MEDIUM)
    queued("high", hours_waiting=1, risk=RiskLevel.HIGH)
    assert [take(client) for _ in range(3)] == ["high", "medium", "low"]


def test_longest_wait_breaks_a_risk_tie(client):
    queued("newer", hours_waiting=1)
    queued("older", hours_waiting=5)
    assert [take(client) for _ in range(2)] == ["older", "newer"]


def test_sla_breach_outranks_risk(client):
    queued("high-fresh", hours_waiting=1, risk=RiskLevel.HIGH)
    queued("low-overdue", hours_waiting=30, risk=RiskLevel.LOW)
    assert take(client) == "low-overdue"


def test_overdue_cases_go_by_how_overdue_they_are(client):
    queued("high-overdue", hours_waiting=26, risk=RiskLevel.HIGH)
    queued("low-more-overdue", hours_waiting=40, risk=RiskLevel.LOW)
    assert take(client) == "low-more-overdue"


def test_sla_window_comes_from_settings(client, monkeypatch):
    monkeypatch.setenv("REVIEW_SLA_HOURS", "2")
    get_settings.cache_clear()
    queued("high-fresh", hours_waiting=1, risk=RiskLevel.HIGH)
    queued("low-overdue", hours_waiting=3, risk=RiskLevel.LOW)
    assert take(client) == "low-overdue"


def test_expired_claim_keeps_its_place_in_line(client):
    queued("first", hours_waiting=20, risk=RiskLevel.HIGH)
    queued("second", hours_waiting=5, risk=RiskLevel.HIGH)
    assert take(client) == "first"
    with SessionLocal() as db:
        db.execute(
            update(Applicant)
            .where(Applicant.id == "first")
            .values(claimed_at=datetime.now(timezone.utc) - timedelta(minutes=300))
        )
        db.commit()
    assert take(client) == "first"
    assert take(client) == "second"


def test_skips_assigned_and_non_review_applicants(client):
    queued("taken")
    queued("waiting", hours_waiting=0.5)
    assert client.post("/v1/review/taken/claim", headers=REVIEWER).status_code == 200
    with SessionLocal() as db:
        db.execute(
            update(Applicant)
            .where(Applicant.id == "waiting")
            .values(status=ApplicantStatus.SUBMITTED)
        )
        db.commit()
    assert take(client) is None


def test_cap_blocks_next_and_decisions_free_a_slot(client):
    client.patch("/v1/review/reviewers/Nadia", json={"max_open_cases": 2}, headers=ADMIN)
    for name in ("a", "b", "c"):
        queued(name)
    first, second = take(client), take(client)
    assert len({first, second}) == 2

    blocked = client.post("/v1/review/next", headers=REVIEWER)
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "reviewer_at_capacity"
    assert blocked.json()["details"] == {"open_cases": 2, "max_open_cases": 2}

    client.post(f"/v1/review/{first}/decision", json={"decision": "approve"}, headers=REVIEWER)
    assert take(client) not in {None, first, second}


def test_manual_claims_count_towards_the_cap(client):
    client.patch("/v1/review/reviewers/Nadia", json={"max_open_cases": 1}, headers=ADMIN)
    queued("a")
    queued("b")
    client.post("/v1/review/a/claim", headers=REVIEWER)
    assert client.post("/v1/review/next", headers=REVIEWER).status_code == 409


def test_expired_claims_free_cap_slots(client):
    client.patch("/v1/review/reviewers/Nadia", json={"max_open_cases": 1}, headers=ADMIN)
    queued("a", hours_waiting=2)
    queued("b", hours_waiting=1)
    take(client)
    with SessionLocal() as db:
        db.execute(
            update(Applicant).values(claimed_at=datetime.now(timezone.utc) - timedelta(minutes=300))
        )
        db.commit()
    assert take(client) == "a"


def test_unavailable_reviewer_gets_nothing_and_the_case_stays_queued(client):
    queued("waiting")
    client.patch("/v1/review/reviewers/me", json={"available": False}, headers=REVIEWER)
    response = client.post("/v1/review/next", headers=REVIEWER)
    assert response.status_code == 409 and response.json()["code"] == "reviewer_unavailable"
    assert client.get("/v1/review/queue?unassigned=true", headers=REVIEWER).json()["total"] == 1

    client.patch("/v1/review/reviewers/me", json={"available": True}, headers=REVIEWER)
    assert take(client) == "waiting"


def test_passes_over_applications_the_reviewer_created(client):
    queued("own", hours_waiting=30, risk=RiskLevel.HIGH, created_by="Ops")
    queued("other", hours_waiting=1, risk=RiskLevel.LOW)
    assert take(client, ADMIN) == "other"
    assert take(client, ADMIN) is None
    assert take(client, REVIEWER) == "own"


def test_passes_over_applications_the_reviewer_submitted(client):
    queued("own", hours_waiting=30)
    with SessionLocal() as db:
        db.add(
            StateTransition(
                applicant_id="own",
                from_status="draft",
                to_status="submitted",
                actor="Ops",
                actor_role="admin",
            )
        )
        db.commit()
    assert take(client, ADMIN) is None
    assert take(client, REVIEWER) == "own"


def test_two_reviewers_get_different_cases(client, monkeypatch):
    second = use_two_reviewers(monkeypatch)
    queued("a", hours_waiting=2)
    queued("b", hours_waiting=1)
    assert take(client, REVIEWER) == "a"
    assert take(client, second) == "b"
    assert client.post("/v1/review/a/claim", headers=second).status_code == 409
    assert (
        client.post(
            "/v1/review/a/decision", json={"decision": "approve"}, headers=second
        ).status_code
        == 403
    )


def test_selection_locks_with_skip_locked_on_postgres():
    statement = review._next_candidate("Nadia", get_settings(), datetime.now(timezone.utc))
    assert "FOR UPDATE SKIP LOCKED" in str(statement.compile(dialect=postgresql.dialect()))
