from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Applicant, ApplicantStatus, StateTransition
from ..schemas import DailyStats, SlaBucket, SlaStats, StatusCount
from ..workflow import TERMINAL_STATES

OPEN_STATES = (
    ApplicantStatus.SUBMITTED,
    ApplicantStatus.UNDER_REVIEW,
    ApplicantStatus.NEEDS_MORE_INFO,
)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def daily_stats(db: Session, day: date) -> DailyStats:
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    end = start + timedelta(days=1)

    created = db.execute(
        select(func.count())
        .select_from(Applicant)
        .where(Applicant.created_at >= start, Applicant.created_at < end)
    ).scalar_one()

    transitions = (
        db.execute(
            select(StateTransition).where(
                StateTransition.created_at >= start, StateTransition.created_at < end
            )
        )
        .scalars()
        .all()
    )
    counts: dict[str, int] = defaultdict(int)
    auto_approved = 0
    for tr in transitions:
        counts[tr.to_status] += 1
        if tr.to_status == ApplicantStatus.APPROVED.value and tr.actor_role == "system":
            auto_approved += 1

    decided = (
        db.execute(
            select(Applicant).where(
                Applicant.decided_at >= start,
                Applicant.decided_at < end,
                Applicant.status.in_(TERMINAL_STATES),
            )
        )
        .scalars()
        .all()
    )
    by_risk: dict[str, int] = defaultdict(int)
    hours: list[float] = []
    for applicant in decided:
        by_risk[applicant.risk_level.value if applicant.risk_level else "unscored"] += 1
        if applicant.submitted_at and applicant.decided_at:
            hours.append(
                (_aware(applicant.decided_at) - _aware(applicant.submitted_at)).total_seconds()
                / 3600
            )

    open_rows = db.execute(
        select(Applicant.status, func.count())
        .where(Applicant.status.in_(OPEN_STATES))
        .group_by(Applicant.status)
    ).all()

    return DailyStats(
        date=day,
        created=created,
        submitted=counts[ApplicantStatus.SUBMITTED.value],
        approved=counts[ApplicantStatus.APPROVED.value],
        rejected=counts[ApplicantStatus.REJECTED.value],
        needs_more_info=counts[ApplicantStatus.NEEDS_MORE_INFO.value],
        auto_approved=auto_approved,
        by_risk_level=dict(by_risk),
        avg_decision_hours=round(sum(hours) / len(hours), 2) if hours else None,
        open_by_status=[StatusCount(status=s, count=c) for s, c in open_rows],
    )


def sla_stats(db: Session, settings: Settings, now: datetime | None = None) -> SlaStats:
    now = now or datetime.now(timezone.utc)
    rows = db.execute(
        select(
            StateTransition.from_status,
            func.avg(StateTransition.seconds_in_previous_state),
            func.max(StateTransition.seconds_in_previous_state),
            func.count(),
        )
        .where(StateTransition.from_status.is_not(None))
        .group_by(StateTransition.from_status)
    ).all()
    buckets = [
        SlaBucket(
            status=ApplicantStatus(row[0]),
            avg_hours=round(row[1] / 3600, 3) if row[1] is not None else None,
            max_hours=round(row[2] / 3600, 3) if row[2] is not None else None,
            samples=row[3],
        )
        for row in rows
    ]
    deadline = now - timedelta(hours=settings.review_sla_hours)
    breaching = (
        db.execute(
            select(Applicant.id).where(
                Applicant.status == ApplicantStatus.UNDER_REVIEW,
                Applicant.status_entered_at <= deadline,
            )
        )
        .scalars()
        .all()
    )
    return SlaStats(
        review_sla_hours=settings.review_sla_hours,
        time_in_state=buckets,
        breaching_now=len(breaching),
        breaching_ids=list(breaching),
    )
