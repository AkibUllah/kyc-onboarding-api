"""Applicant state machine.

::

    DRAFT -> SUBMITTED -> UNDER_REVIEW -> APPROVED
                                       -> REJECTED
                                       -> NEEDS_MORE_INFO -> SUBMITTED (re-submission)

Every transition is recorded as a ``StateTransition`` with the actor and the time
spent in the previous state, which is what the SLA statistics are built from.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .errors import InvalidTransitionError
from .models import Applicant, ApplicantStatus, StateTransition, utcnow

TRANSITIONS: dict[ApplicantStatus, frozenset[ApplicantStatus]] = {
    ApplicantStatus.DRAFT: frozenset({ApplicantStatus.SUBMITTED}),
    ApplicantStatus.SUBMITTED: frozenset({ApplicantStatus.UNDER_REVIEW}),
    ApplicantStatus.UNDER_REVIEW: frozenset(
        {ApplicantStatus.APPROVED, ApplicantStatus.REJECTED, ApplicantStatus.NEEDS_MORE_INFO}
    ),
    ApplicantStatus.NEEDS_MORE_INFO: frozenset({ApplicantStatus.SUBMITTED}),
    ApplicantStatus.APPROVED: frozenset(),
    ApplicantStatus.REJECTED: frozenset(),
}

TERMINAL_STATES = frozenset({ApplicantStatus.APPROVED, ApplicantStatus.REJECTED})
EDITABLE_STATES = frozenset({ApplicantStatus.DRAFT, ApplicantStatus.NEEDS_MORE_INFO})


def can_transition(current: ApplicantStatus, target: ApplicantStatus) -> bool:
    return target in TRANSITIONS.get(current, frozenset())


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def transition(
    applicant: Applicant,
    target: ApplicantStatus,
    *,
    actor: str,
    actor_role: str,
    reason: str = "",
    now: datetime | None = None,
) -> StateTransition:
    """Move ``applicant`` to ``target`` or raise ``InvalidTransitionError``."""
    if not can_transition(applicant.status, target):
        raise InvalidTransitionError(
            f"Cannot move applicant from '{applicant.status.value}' to '{target.value}'.",
            details={
                "from": applicant.status.value,
                "to": target.value,
                "allowed": sorted(s.value for s in TRANSITIONS.get(applicant.status, ())),
            },
        )
    now = now or utcnow()
    elapsed = (now - _aware(applicant.status_entered_at)).total_seconds()
    record = StateTransition(
        applicant=applicant,
        from_status=applicant.status.value,
        to_status=target.value,
        actor=actor,
        actor_role=actor_role,
        reason=reason,
        seconds_in_previous_state=max(elapsed, 0.0),
        created_at=now,
    )
    applicant.status = target
    applicant.status_entered_at = now
    if target == ApplicantStatus.SUBMITTED:
        applicant.submitted_at = now
    if target in TERMINAL_STATES or target == ApplicantStatus.NEEDS_MORE_INFO:
        applicant.decided_at = now
        applicant.decision_reason = reason
    return record
