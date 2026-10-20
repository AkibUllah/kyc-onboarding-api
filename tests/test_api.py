from datetime import date, datetime, timedelta, timezone

from kyc.database import SessionLocal
from kyc.models import Applicant, ApplicantStatus
from tests.conftest import (
    ADMIN,
    AGENT,
    JPEG,
    PDF,
    PNG,
    REVIEWER,
    applicant_payload,
    upload,
    upload_all,
)


# --- auth -------------------------------------------------------------------
def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["database"] == "ok"


def test_api_key_required_and_role_enforced(client):
    assert client.get("/v1/applicants").status_code == 403
    assert client.get("/v1/applicants", headers={"X-API-Key": "nope"}).status_code == 403
    response = client.get("/v1/review/queue", headers=AGENT)
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"
    assert client.get("/v1/review/queue", headers=REVIEWER).status_code == 200
    assert client.get("/v1/review/queue", headers=ADMIN).status_code == 200  # admin inherits


# --- applicants + documents --------------------------------------------------
def test_create_applicant_validates_input(client):
    bad = client.post("/v1/applicants", json=applicant_payload(phone="12345"), headers=AGENT)
    assert bad.status_code == 422
    bad_email = client.post(
        "/v1/applicants", json=applicant_payload(email="not-an-email"), headers=AGENT
    )
    assert bad_email.status_code == 422
    ok = client.post(
        "/v1/applicants", json=applicant_payload(nid_number="1990-1234 56789"), headers=AGENT
    )
    assert ok.status_code == 201
    body = ok.json()
    assert body["status"] == "draft"
    assert body["nid_number"] == "1990123456789"
    assert set(body["missing_documents"]) == {"nid_front", "nid_back", "selfie"}
    assert body["email"] == "rahim@example.com"


def test_upload_documents_and_download(client, create_applicant):
    applicant = create_applicant()
    response = upload(client, applicant["id"], "nid_front", JPEG, filename="../../etc/passwd.jpg")
    assert response.status_code == 201, response.text
    doc = response.json()
    assert doc["content_type"] == "image/jpeg"
    assert doc["original_filename"] == "passwd.jpg"  # path components stripped
    assert len(doc["sha256"]) == 64

    assert upload(client, applicant["id"], "nid_front", PNG, "image/png").status_code == 201
    docs = client.get(f"/v1/applicants/{applicant['id']}/documents", headers=AGENT).json()
    assert len(docs) == 1  # replaced, not duplicated
    assert docs[0]["content_type"] == "image/png"

    content = client.get(
        f"/v1/applicants/{applicant['id']}/documents/{docs[0]['id']}/content", headers=REVIEWER
    )
    assert content.status_code == 200 and content.content == PNG
    assert (
        client.get(
            f"/v1/applicants/{applicant['id']}/documents/{docs[0]['id']}/content", headers=AGENT
        ).status_code
        == 403
    )


def test_upload_rejections(client, create_applicant):
    applicant = create_applicant()
    spoofed = upload(client, applicant["id"], "nid_front", PNG, "image/jpeg")
    assert spoofed.status_code == 400 and spoofed.json()["code"] == "upload_rejected"
    assert (
        upload(client, applicant["id"], "selfie", PDF, "application/pdf", "s.pdf").status_code
        == 400
    )
    assert (
        upload(
            client, applicant["id"], "nid_front", b"GIF89a" + b"\x00" * 20, "image/gif"
        ).status_code
        == 400
    )
    assert upload(client, applicant["id"], "passport", JPEG).status_code == 422


def test_submit_requires_all_documents(client, create_applicant):
    applicant = create_applicant()
    upload(client, applicant["id"], "nid_front", JPEG)
    response = client.post(f"/v1/applicants/{applicant['id']}/submit", headers=AGENT)
    assert response.status_code == 422
    assert set(response.json()["details"]["missing_documents"]) == {"nid_back", "selfie"}


def test_clean_applicant_is_auto_approved_after_background_checks(client, create_applicant):
    applicant = create_applicant()
    upload_all(client, applicant["id"])
    response = client.post(f"/v1/applicants/{applicant['id']}/submit", headers=AGENT)
    assert response.status_code == 202
    assert response.json()["status"] == "submitted"

    # TestClient runs background tasks before returning, so checks are done now.
    final = client.get(f"/v1/applicants/{applicant['id']}", headers=AGENT).json()
    assert final["status"] == "approved"
    assert final["risk_level"] == "low" and final["risk_score"] == 0
    assert final["assigned_reviewer"] == "system"

    checks = client.get(f"/v1/applicants/{applicant['id']}/checks", headers=REVIEWER).json()
    assert {c["name"] for c in checks} == {
        "nid_format",
        "dob_consistency",
        "face_match",
        "sanctions_pep",
        "duplicate_identity",
    }
    assert all(c["passed"] for c in checks)

    history = [
        h["to_status"]
        for h in client.get(f"/v1/applicants/{applicant['id']}/history", headers=AGENT).json()
    ]
    assert history == ["draft", "submitted", "under_review", "approved"]


def test_editing_blocked_after_submission(client, create_applicant):
    applicant = create_applicant()
    assert (
        client.patch(
            f"/v1/applicants/{applicant['id']}", json={"occupation": "Farmer"}, headers=AGENT
        ).json()["occupation"]
        == "Farmer"
    )
    upload_all(client, applicant["id"])
    client.post(f"/v1/applicants/{applicant['id']}/submit", headers=AGENT)
    blocked = client.patch(
        f"/v1/applicants/{applicant['id']}", json={"occupation": "x"}, headers=AGENT
    )
    assert blocked.status_code == 409 and blocked.json()["code"] == "invalid_transition"
    assert upload(client, applicant["id"], "selfie", PNG, "image/png").status_code == 409


# --- risk paths ---------------------------------------------------------------
def test_sanctions_hit_lands_in_review_queue(client, create_applicant):
    applicant = create_applicant(
        full_name="Abdul Karim Chowdhury",
        date_of_birth="1968-03-14",
        nid_number="19681234567890123",
    )
    upload_all(client, applicant["id"])
    client.post(f"/v1/applicants/{applicant['id']}/submit", headers=AGENT)
    body = client.get(f"/v1/applicants/{applicant['id']}", headers=REVIEWER).json()
    assert body["status"] == "under_review"
    assert body["risk_level"] == "high" and body["risk_score"] >= 70
    checks = {
        c["name"]: c
        for c in client.get(f"/v1/applicants/{applicant['id']}/checks", headers=REVIEWER).json()
    }
    assert checks["sanctions_pep"]["passed"] is False
    assert checks["sanctions_pep"]["details"]["matches"][0]["reference"] == "SMPL-0001"
    queue = client.get("/v1/review/queue?risk_level=high", headers=REVIEWER).json()
    assert queue["total"] == 1 and queue["items"][0]["id"] == applicant["id"]


def test_pep_match_is_medium_risk_not_auto_approved(client, create_applicant):
    applicant = create_applicant(
        full_name="Shahida Begum", date_of_birth="1962-07-21", nid_number="1962123456789"
    )
    upload_all(client, applicant["id"])
    client.post(f"/v1/applicants/{applicant['id']}/submit", headers=AGENT)
    body = client.get(f"/v1/applicants/{applicant['id']}", headers=REVIEWER).json()
    assert body["status"] == "under_review" and body["risk_level"] == "medium"


def test_underage_and_nid_year_mismatch_fail_dob_check(client, create_applicant):
    minor_dob = (date.today() - timedelta(days=17 * 365)).isoformat()
    applicant = create_applicant(date_of_birth=minor_dob, nid_number="19901234567890123")
    upload_all(client, applicant["id"])
    client.post(f"/v1/applicants/{applicant['id']}/submit", headers=AGENT)
    checks = {
        c["name"]: c
        for c in client.get(f"/v1/applicants/{applicant['id']}/checks", headers=REVIEWER).json()
    }
    dob = checks["dob_consistency"]
    assert dob["passed"] is False
    assert set(dob["details"]["problems"]) == {"under_18", "nid_year_mismatch"}


def test_identical_selfie_and_nid_photo_fails_face_match(client, create_applicant):
    applicant = create_applicant()
    upload_all(client, applicant["id"], selfie=JPEG, nid_front=JPEG)
    client.post(f"/v1/applicants/{applicant['id']}/submit", headers=AGENT)
    checks = {
        c["name"]: c
        for c in client.get(f"/v1/applicants/{applicant['id']}/checks", headers=REVIEWER).json()
    }
    assert checks["face_match"]["passed"] is False
    assert checks["face_match"]["details"]["identical_files"] is True
    body = client.get(f"/v1/applicants/{applicant['id']}", headers=REVIEWER).json()
    assert body["status"] == "under_review" and body["risk_level"] == "medium"


def test_reused_document_and_duplicate_nid_are_flagged(client, create_applicant):
    first = create_applicant()
    upload_all(client, first["id"])
    client.post(f"/v1/applicants/{first['id']}/submit", headers=AGENT)
    assert client.get(f"/v1/applicants/{first['id']}", headers=AGENT).json()["status"] == "approved"

    second = create_applicant(
        full_name="Karim Hossain Mia", phone="01811111111"
    )  # same NID, same files
    upload_all(client, second["id"])
    client.post(f"/v1/applicants/{second['id']}/submit", headers=AGENT)
    checks = {
        c["name"]: c
        for c in client.get(f"/v1/applicants/{second['id']}/checks", headers=REVIEWER).json()
    }
    dup = checks["duplicate_identity"]
    assert dup["passed"] is False
    assert set(dup["details"]["problems"]) == {"document_reused", "nid_already_approved"}
    assert first["id"] in dup["details"]["other_applications_with_nid"]
    assert (
        client.get(f"/v1/applicants/{second['id']}", headers=REVIEWER).json()["risk_level"]
        == "high"
    )


# --- reviewer flows -------------------------------------------------------------
def _submit_high_risk(client, create_applicant, **overrides):
    applicant = create_applicant(
        full_name="Tanvir Ahmed Siddique",
        date_of_birth="1983-05-30",
        nid_number="1983123456789",
        **overrides,
    )
    upload_all(client, applicant["id"])
    client.post(f"/v1/applicants/{applicant['id']}/submit", headers=AGENT)
    return applicant["id"]


def test_reviewer_claim_and_reject_requires_reason(client, create_applicant):
    applicant_id = _submit_high_risk(client, create_applicant)
    claimed = client.post(f"/v1/review/{applicant_id}/claim", headers=REVIEWER).json()
    assert claimed["assigned_reviewer"] == "Nadia"

    no_reason = client.post(
        f"/v1/review/{applicant_id}/decision", json={"decision": "reject"}, headers=REVIEWER
    )
    assert no_reason.status_code == 409

    rejected = client.post(
        f"/v1/review/{applicant_id}/decision",
        json={"decision": "reject", "reason": "Confirmed sanctions match"},
        headers=REVIEWER,
    ).json()
    assert rejected["status"] == "rejected"
    assert rejected["decision_reason"] == "Confirmed sanctions match"
    # terminal: nothing else allowed
    again = client.post(
        f"/v1/review/{applicant_id}/decision", json={"decision": "approve"}, headers=REVIEWER
    )
    assert again.status_code == 409
    assert client.get("/v1/review/queue", headers=REVIEWER).json()["total"] == 0


def test_claimed_applicant_cannot_be_decided_by_another_reviewer(
    client, create_applicant, monkeypatch
):
    monkeypatch.setenv(
        "API_KEYS",
        "agent-key:agent:Branch App,reviewer-key:reviewer:Nadia,"
        "reviewer2-key:reviewer:Farhan,admin-key:admin:Ops",
    )
    from kyc.config import get_settings

    get_settings.cache_clear()
    applicant_id = _submit_high_risk(client, create_applicant)
    client.post(f"/v1/review/{applicant_id}/claim", headers=REVIEWER)
    other = client.post(f"/v1/review/{applicant_id}/claim", headers={"X-API-Key": "reviewer2-key"})
    assert other.status_code == 409
    denied = client.post(
        f"/v1/review/{applicant_id}/decision",
        json={"decision": "approve"},
        headers={"X-API-Key": "reviewer2-key"},
    )
    assert denied.status_code == 403
    # admin can override
    approved = client.post(
        f"/v1/review/{applicant_id}/decision",
        json={"decision": "approve", "reason": "Manual override"},
        headers=ADMIN,
    )
    assert approved.status_code == 200 and approved.json()["status"] == "approved"


def test_needs_more_info_then_resubmission(client, create_applicant):
    applicant_id = _submit_high_risk(client, create_applicant)
    response = client.post(
        f"/v1/review/{applicant_id}/decision",
        json={"decision": "needs_more_info", "reason": "NID back unreadable"},
        headers=REVIEWER,
    )
    assert response.json()["status"] == "needs_more_info"
    # agent can now replace the document and resubmit
    assert upload(client, applicant_id, "nid_back", PNG, "image/png").status_code == 201
    assert client.post(f"/v1/applicants/{applicant_id}/submit", headers=AGENT).status_code == 202
    body = client.get(f"/v1/applicants/{applicant_id}", headers=REVIEWER).json()
    assert body["status"] == "under_review"
    history = [
        h["to_status"]
        for h in client.get(f"/v1/applicants/{applicant_id}/history", headers=AGENT).json()
    ]
    assert history == [
        "draft",
        "submitted",
        "under_review",
        "needs_more_info",
        "submitted",
        "under_review",
    ]
    checks = client.get(f"/v1/applicants/{applicant_id}/checks", headers=REVIEWER).json()
    assert len({c["run_id"] for c in checks}) == 2  # two check runs recorded


def test_admin_rerun_checks(client, create_applicant):
    applicant_id = _submit_high_risk(client, create_applicant)
    response = client.post(f"/v1/applicants/{applicant_id}/checks/run", headers=ADMIN)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "under_review" and body["risk_level"] == "high"
    assert len(body["results"]) == 5
    assert (
        client.post(f"/v1/applicants/{applicant_id}/checks/run", headers=REVIEWER).status_code
        == 403
    )


# --- stats --------------------------------------------------------------------
def test_daily_and_sla_stats(client, create_applicant):
    clean = create_applicant()
    upload_all(client, clean["id"])
    client.post(f"/v1/applicants/{clean['id']}/submit", headers=AGENT)
    risky_id = _submit_high_risk(client, create_applicant, phone="01911111111")

    today = datetime.now(timezone.utc).date().isoformat()
    daily = client.get(f"/v1/stats/daily?day={today}", headers=REVIEWER).json()
    assert daily["created"] == 2
    assert daily["submitted"] == 2
    assert daily["approved"] == 1 and daily["auto_approved"] == 1
    assert daily["by_risk_level"] == {"low": 1}
    assert daily["avg_decision_hours"] is not None
    assert daily["open_by_status"] == [{"status": "under_review", "count": 1}]

    # Push the risky applicant past the SLA and check it is reported as breaching.
    with SessionLocal() as db:
        row = db.get(Applicant, risky_id)
        row.status_entered_at = datetime.now(timezone.utc) - timedelta(hours=30)
        db.commit()
    sla = client.get("/v1/stats/sla", headers=ADMIN).json()
    assert sla["review_sla_hours"] == 24
    assert sla["breaching_now"] == 1 and sla["breaching_ids"] == [risky_id]
    states = {b["status"]: b for b in sla["time_in_state"]}
    assert states["draft"]["samples"] == 2 and states["submitted"]["samples"] == 2
    assert states["under_review"]["samples"] == 1

    assert client.get("/v1/stats/daily", headers=AGENT).status_code == 403
    assert ApplicantStatus.UNDER_REVIEW.value == "under_review"
