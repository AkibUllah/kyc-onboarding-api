from datetime import date, datetime, timedelta, timezone

import pytest

from kyc.checks.base import CheckOutcome
from kyc.checks.risk import assess, profile_factors, score_to_level
from kyc.config import get_settings
from kyc.errors import InvalidTransitionError
from kyc.models import Applicant, ApplicantStatus, RiskLevel
from kyc.workflow import EDITABLE_STATES, can_transition, transition


def make_applicant(**overrides) -> Applicant:
    fields = dict(
        full_name="Test Person",
        nid_number="1234567890",
        date_of_birth=date(1990, 1, 1),
        phone="01711111111",
        email="t@example.com",
        address="Dhaka",
        occupation="Teacher",
        status=ApplicantStatus.DRAFT,
        status_entered_at=datetime.now(timezone.utc),
    )
    fields.update(overrides)
    return Applicant(**fields)


def test_score_to_level_thresholds():
    settings = get_settings()
    assert score_to_level(0, settings) is RiskLevel.LOW
    assert score_to_level(29, settings) is RiskLevel.LOW
    assert score_to_level(30, settings) is RiskLevel.MEDIUM
    assert score_to_level(59, settings) is RiskLevel.MEDIUM
    assert score_to_level(60, settings) is RiskLevel.HIGH


def test_assess_sums_failed_checks_and_profile_factors():
    settings = get_settings()
    outcomes = [
        CheckOutcome("nid_format", True),
        CheckOutcome("face_match", False, 45),
        CheckOutcome("sanctions_pep", False, 30),
    ]
    clean = assess(make_applicant(), outcomes, settings)
    assert clean.score == 75 and clean.level is RiskLevel.HIGH
    assert clean.factors == ["check:face_match", "check:sanctions_pep"]

    sparse = make_applicant(email="", address="", occupation="", monthly_income_bdt=600_000)
    assert dict(profile_factors(sparse)) == {
        "no_email": 5,
        "no_address": 10,
        "high_declared_income": 10,
        "no_occupation": 5,
    }
    result = assess(sparse, [CheckOutcome("x", True)], settings)
    assert result.score == 30 and result.level is RiskLevel.MEDIUM


def test_assess_caps_score_at_100():
    result = assess(
        make_applicant(),
        [CheckOutcome("a", False, 90), CheckOutcome("b", False, 90)],
        get_settings(),
    )
    assert result.score == 100


def test_workflow_happy_path_records_elapsed_time():
    start = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
    applicant = make_applicant(status_entered_at=start)
    t1 = transition(
        applicant,
        ApplicantStatus.SUBMITTED,
        actor="app",
        actor_role="agent",
        now=start + timedelta(minutes=30),
    )
    assert applicant.status is ApplicantStatus.SUBMITTED
    assert applicant.submitted_at == start + timedelta(minutes=30)
    assert t1.seconds_in_previous_state == 1800
    transition(
        applicant,
        ApplicantStatus.UNDER_REVIEW,
        actor="system",
        actor_role="system",
        now=start + timedelta(minutes=31),
    )
    t3 = transition(
        applicant,
        ApplicantStatus.APPROVED,
        actor="Nadia",
        actor_role="reviewer",
        reason="All good",
        now=start + timedelta(hours=2),
    )
    assert t3.from_status == "under_review" and t3.seconds_in_previous_state == 89 * 60
    assert applicant.decided_at == start + timedelta(hours=2)
    assert applicant.decision_reason == "All good"


def test_workflow_rejects_invalid_transitions():
    applicant = make_applicant()
    with pytest.raises(InvalidTransitionError) as exc:
        transition(applicant, ApplicantStatus.APPROVED, actor="x", actor_role="admin")
    assert exc.value.details["allowed"] == ["submitted"]
    assert not can_transition(ApplicantStatus.APPROVED, ApplicantStatus.REJECTED)
    assert not can_transition(ApplicantStatus.REJECTED, ApplicantStatus.SUBMITTED)
    assert not can_transition(ApplicantStatus.SUBMITTED, ApplicantStatus.APPROVED)


def test_needs_more_info_loops_back_to_submitted():
    applicant = make_applicant(status=ApplicantStatus.UNDER_REVIEW)
    transition(
        applicant,
        ApplicantStatus.NEEDS_MORE_INFO,
        actor="Nadia",
        actor_role="reviewer",
        reason="Blurry NID back",
    )
    assert applicant.status in EDITABLE_STATES
    transition(applicant, ApplicantStatus.SUBMITTED, actor="app", actor_role="agent")
    assert applicant.status is ApplicantStatus.SUBMITTED
