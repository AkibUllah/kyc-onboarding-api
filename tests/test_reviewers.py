from __future__ import annotations

from kyc.config import get_settings
from tests.conftest import (
    ADMIN,
    AGENT,
    REVIEWER,
    applicant_payload,
    submit_for_review,
    upload_all,
    use_two_reviewers,
)


def roster(client) -> dict[str, dict]:
    return {r["name"]: r for r in client.get("/v1/review/reviewers", headers=REVIEWER).json()}


def test_roster_lists_reviewers_with_defaults_and_load(client, create_applicant):
    applicant_id = submit_for_review(client, create_applicant)
    client.post(f"/v1/review/{applicant_id}/claim", headers=REVIEWER)
    assert roster(client) == {
        "Nadia": {"name": "Nadia", "available": True, "max_open_cases": 10, "open_cases": 1},
        "Ops": {"name": "Ops", "available": True, "max_open_cases": 10, "open_cases": 0},
    }
    assert client.get("/v1/review/reviewers", headers=AGENT).status_code == 403


def test_reviewer_sets_own_availability_but_not_the_cap(client):
    off = client.patch("/v1/review/reviewers/me", json={"available": False}, headers=REVIEWER)
    assert off.status_code == 200 and off.json()["available"] is False
    assert roster(client)["Nadia"]["available"] is False

    capped = client.patch(
        "/v1/review/reviewers/Nadia", json={"max_open_cases": 99}, headers=REVIEWER
    )
    assert capped.status_code == 403
    assert client.patch("/v1/review/reviewers/me", json={}, headers=AGENT).status_code == 403


def test_admin_overrides_cap_and_clears_it_with_null(client):
    body = client.patch(
        "/v1/review/reviewers/Nadia", json={"max_open_cases": 2}, headers=ADMIN
    ).json()
    assert body["max_open_cases"] == 2 and body["available"] is True

    paused = client.patch(
        "/v1/review/reviewers/Nadia", json={"available": False}, headers=ADMIN
    ).json()
    assert paused["max_open_cases"] == 2 and paused["available"] is False  # partial update

    cleared = client.patch(
        "/v1/review/reviewers/Nadia", json={"max_open_cases": None}, headers=ADMIN
    ).json()
    assert cleared["max_open_cases"] == 10


def test_cap_must_be_positive_and_reviewer_must_exist(client):
    invalid = client.patch("/v1/review/reviewers/Nadia", json={"max_open_cases": 0}, headers=ADMIN)
    assert invalid.status_code == 422
    unknown = client.patch("/v1/review/reviewers/Nobody", json={"available": True}, headers=ADMIN)
    assert unknown.status_code == 404
    agent = client.patch("/v1/review/reviewers/Branch App", json={"available": True}, headers=ADMIN)
    assert agent.status_code == 404


def test_unavailable_reviewer_cannot_claim(client, create_applicant):
    applicant_id = submit_for_review(client, create_applicant)
    client.patch("/v1/review/reviewers/me", json={"available": False}, headers=REVIEWER)

    response = client.post(f"/v1/review/{applicant_id}/claim", headers=REVIEWER)
    assert response.status_code == 409 and response.json()["code"] == "reviewer_unavailable"
    body = client.get(f"/v1/applicants/{applicant_id}", headers=REVIEWER).json()
    assert body["assigned_reviewer"] == ""

    client.patch("/v1/review/reviewers/me", json={"available": True}, headers=REVIEWER)
    assert client.post(f"/v1/review/{applicant_id}/claim", headers=REVIEWER).status_code == 200


def test_unavailable_reviewer_finishes_held_cases_but_takes_no_others(client, create_applicant):
    held = submit_for_review(client, create_applicant)
    other = submit_for_review(client, create_applicant, phone="01911111111")
    client.post(f"/v1/review/{held}/claim", headers=REVIEWER)
    client.patch("/v1/review/reviewers/me", json={"available": False}, headers=REVIEWER)

    finished = client.post(
        f"/v1/review/{held}/decision", json={"decision": "approve"}, headers=REVIEWER
    )
    assert finished.status_code == 200
    taken = client.post(
        f"/v1/review/{other}/decision", json={"decision": "approve"}, headers=REVIEWER
    )
    assert taken.status_code == 409 and taken.json()["code"] == "reviewer_unavailable"


def test_unavailable_reviewer_cannot_renew_a_claim(client, create_applicant):
    applicant_id = submit_for_review(client, create_applicant)
    client.post(f"/v1/review/{applicant_id}/claim", headers=REVIEWER)
    client.patch("/v1/review/reviewers/me", json={"available": False}, headers=REVIEWER)
    renewed = client.post(f"/v1/review/{applicant_id}/claim", headers=REVIEWER)
    assert renewed.status_code == 409


def test_manual_claim_respects_the_cap(client, create_applicant, monkeypatch):
    monkeypatch.setenv("MAX_OPEN_CASES", "1")
    get_settings.cache_clear()
    first = submit_for_review(client, create_applicant)
    second = submit_for_review(client, create_applicant, phone="01911111111")
    assert client.post(f"/v1/review/{first}/claim", headers=REVIEWER).status_code == 200
    assert client.post(f"/v1/review/{first}/claim", headers=REVIEWER).status_code == 200  # renewal

    blocked = client.post(f"/v1/review/{second}/claim", headers=REVIEWER)
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "reviewer_at_capacity"
    assert blocked.json()["details"] == {"open_cases": 1, "max_open_cases": 1}

    client.post(f"/v1/review/{first}/decision", json={"decision": "approve"}, headers=REVIEWER)
    assert client.post(f"/v1/review/{second}/claim", headers=REVIEWER).status_code == 200


def test_cap_is_per_reviewer(client, create_applicant, monkeypatch):
    second_reviewer = use_two_reviewers(monkeypatch)
    client.patch("/v1/review/reviewers/Nadia", json={"max_open_cases": 1}, headers=ADMIN)
    first = submit_for_review(client, create_applicant)
    second = submit_for_review(client, create_applicant, phone="01911111111")
    client.post(f"/v1/review/{first}/claim", headers=REVIEWER)
    assert client.post(f"/v1/review/{second}/claim", headers=REVIEWER).status_code == 409
    assert client.post(f"/v1/review/{second}/claim", headers=second_reviewer).status_code == 200


def test_submitter_cannot_claim_or_decide_their_own_application(client, create_applicant):
    applicant = create_applicant(
        full_name="Tanvir Ahmed Siddique", date_of_birth="1983-05-30", nid_number="1983123456789"
    )
    upload_all(client, applicant["id"])
    client.post(f"/v1/applicants/{applicant['id']}/submit", headers=ADMIN)  # Ops submits

    claimed = client.post(f"/v1/review/{applicant['id']}/claim", headers=ADMIN)
    assert claimed.status_code == 403 and claimed.json()["code"] == "forbidden"
    decided = client.post(
        f"/v1/review/{applicant['id']}/decision", json={"decision": "approve"}, headers=ADMIN
    )
    assert decided.status_code == 403
    body = client.get(f"/v1/applicants/{applicant['id']}", headers=ADMIN).json()
    assert body["status"] == "under_review" and body["assigned_reviewer"] == ""


def test_creator_counts_as_submitter(client):
    response = client.post(
        "/v1/applicants",
        json=applicant_payload(
            full_name="Tanvir Ahmed Siddique",
            date_of_birth="1983-05-30",
            nid_number="1983123456789",
        ),
        headers=ADMIN,
    )
    applicant_id = response.json()["id"]
    upload_all(client, applicant_id)
    client.post(f"/v1/applicants/{applicant_id}/submit", headers=AGENT)
    assert client.post(f"/v1/review/{applicant_id}/claim", headers=ADMIN).status_code == 403


def test_earlier_submitter_stays_excluded_after_someone_else_resubmits(client, create_applicant):
    applicant = create_applicant(
        full_name="Tanvir Ahmed Siddique", date_of_birth="1983-05-30", nid_number="1983123456789"
    )
    upload_all(client, applicant["id"])
    client.post(f"/v1/applicants/{applicant['id']}/submit", headers=ADMIN)
    client.post(
        f"/v1/review/{applicant['id']}/decision",
        json={"decision": "needs_more_info", "reason": "Blurred selfie"},
        headers=REVIEWER,
    )
    client.post(f"/v1/applicants/{applicant['id']}/submit", headers=AGENT)
    assert client.post(f"/v1/review/{applicant['id']}/claim", headers=ADMIN).status_code == 403
    assert client.post(f"/v1/review/{applicant['id']}/claim", headers=REVIEWER).status_code == 200
