"""Real threads against a real connection pool: file-backed SQLite by default, PostgreSQL when
TEST_DATABASE_URL is set. In-memory SQLite shares one connection, so it cannot race."""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from kyc.auth import Principal
from kyc.config import get_settings
from kyc.database import Base, SessionLocal, engine
from kyc.errors import InvalidTransitionError, KycError, ReviewerAtCapacity
from kyc.models import Applicant, ApplicantStatus, AssignmentEvent, RiskLevel
from kyc.services import review
from tests.conftest import queued

WORKERS = 8
postgres_only = pytest.mark.skipif(
    engine.dialect.name != "postgresql", reason="needs row locks and a concurrent database"
)


def reviewer(index: int) -> Principal:
    return Principal(name=f"Reviewer {index}", role="reviewer")


@pytest.fixture
def sessions(tmp_path):
    if engine.dialect.name == "postgresql":
        yield SessionLocal
        return
    file_engine = create_engine(
        f"sqlite:///{tmp_path / 'race.db'}",
        connect_args={"check_same_thread": False, "timeout": 60},
    )
    # Durability is irrelevant here and fsync latency only makes writers time out.
    event.listen(file_engine, "connect", lambda conn, _: conn.execute("PRAGMA synchronous=OFF"))
    Base.metadata.create_all(bind=file_engine)
    yield sessionmaker(bind=file_engine, autoflush=False, expire_on_commit=False)
    file_engine.dispose()


def race(sessions, work: Callable[[Any, int], Any], workers: int = WORKERS) -> list[Any]:
    """Run ``work`` on every worker at once; a ``KycError`` counts as that worker's result."""
    barrier = threading.Barrier(workers)

    def run(index: int) -> Any:
        with sessions() as db:
            barrier.wait()
            try:
                return work(db, index)
            except KycError as exc:
                return exc

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(run, range(workers)))


def holders(sessions) -> dict[str, str]:
    with sessions() as db:
        rows = db.execute(select(Applicant.id, Applicant.assigned_reviewer)).all()
    return dict(rows)


def assignment_counts(sessions) -> Counter:
    with sessions() as db:
        return Counter(db.execute(select(AssignmentEvent.applicant_id)).scalars())


def test_concurrent_next_hands_each_case_to_one_reviewer(sessions):
    for index in range(5):
        queued(f"case-{index}", hours_waiting=index + 1, session_factory=sessions)

    results = race(sessions, lambda db, i: review.assign_next(db, reviewer(i), get_settings()))

    handed_out = [result.id for result in results if result is not None]
    assert len(handed_out) == len(set(handed_out)) == 5
    assert results.count(None) == WORKERS - 5
    assert assignment_counts(sessions) == {f"case-{index}": 1 for index in range(5)}
    assert len(set(holders(sessions).values())) == 5


def test_repeated_rounds_drain_the_queue_without_duplicates(sessions):
    for index in range(WORKERS * 3):
        queued(f"case-{index}", hours_waiting=index + 1, session_factory=sessions)

    def drain(db, index: int) -> list[str]:
        taken = []
        while (applicant := review.assign_next(db, reviewer(index), get_settings())) is not None:
            taken.append(applicant.id)
            if len(taken) == get_settings().max_open_cases:
                break
        return taken

    handed_out = [case for taken in race(sessions, drain) for case in taken]
    assert len(handed_out) == len(set(handed_out)) == WORKERS * 3


def test_concurrent_next_cannot_overshoot_the_cap(sessions, monkeypatch):
    monkeypatch.setenv("MAX_OPEN_CASES", "3")
    get_settings.cache_clear()
    for index in range(WORKERS):
        queued(f"case-{index}", hours_waiting=index + 1, session_factory=sessions)
    same_reviewer = reviewer(0)

    results = race(sessions, lambda db, _: review.assign_next(db, same_reviewer, get_settings()))

    assert sum(isinstance(result, Applicant) for result in results) == 3
    assert sum(isinstance(result, ReviewerAtCapacity) for result in results) == WORKERS - 3
    assert Counter(holders(sessions).values())[same_reviewer.name] == 3


def test_concurrent_manual_claims_have_one_winner(sessions):
    queued("contested", session_factory=sessions)

    def claim(db, index: int):
        applicant = db.get(Applicant, "contested")
        return review.claim(db, applicant, reviewer(index), get_settings())

    results = race(sessions, claim)

    winners = [result for result in results if isinstance(result, Applicant)]
    assert len(winners) == 1
    assert sum(isinstance(result, InvalidTransitionError) for result in results) == WORKERS - 1
    assert holders(sessions) == {"contested": winners[0].assigned_reviewer}
    assert assignment_counts(sessions) == {"contested": 1}


def test_next_and_manual_claim_cannot_share_a_case(sessions):
    queued("contested", session_factory=sessions)

    def either(db, index: int):
        if index % 2:
            return review.assign_next(db, reviewer(index), get_settings())
        return review.claim(db, db.get(Applicant, "contested"), reviewer(index), get_settings())

    results = race(sessions, either)

    assert sum(isinstance(result, Applicant) for result in results) == 1
    assert assignment_counts(sessions) == {"contested": 1}


@postgres_only
def test_next_passes_over_a_row_locked_by_another_transaction(client):
    queued("locked", hours_waiting=20, risk=RiskLevel.HIGH)
    queued("free", hours_waiting=1, risk=RiskLevel.LOW)

    def assign() -> Applicant | None:
        with SessionLocal() as db:
            return review.assign_next(db, reviewer(1), get_settings())

    with ThreadPoolExecutor(max_workers=1) as pool, SessionLocal() as holder:
        holder.execute(select(Applicant).where(Applicant.id == "locked").with_for_update())
        try:
            assigned = pool.submit(assign).result(timeout=5)  # would block if it waited
        finally:
            holder.rollback()

    assert assigned.id == "free"


@postgres_only
def test_decision_and_next_cannot_both_win(client):
    """The decision takes the row lock, so it never overwrites a fresh assignment."""

    def assign_or_decide(applicant_id: str):
        def work(db, index: int):
            if index == 0:
                return review.assign_next(db, reviewer(0), get_settings())
            return review.decide(
                db,
                db.get(Applicant, applicant_id),
                decision="approve",
                reason="",
                reviewer=reviewer(1),
                settings=get_settings(),
            )

        return work

    for round_number in range(10):
        applicant_id = f"round-{round_number}"
        queued(applicant_id)
        assigned, _ = race(SessionLocal, assign_or_decide(applicant_id), workers=2)
        final = holders(SessionLocal)[applicant_id]
        if isinstance(assigned, Applicant):
            assert final == reviewer(0).name
            with SessionLocal() as db:  # take it out of the way of the next round
                row = db.get(Applicant, applicant_id)
                assert row.status == ApplicantStatus.UNDER_REVIEW
                row.status = ApplicantStatus.REJECTED
                db.commit()
        else:
            assert final == reviewer(1).name
