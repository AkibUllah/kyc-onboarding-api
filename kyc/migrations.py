"""In-place schema upgrades for databases created by an older release.

``create_all`` adds missing tables but never alters existing ones. Until the project moves
to a migration tool, each column added to an existing table gets an idempotent step here.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text, update

from .models import Applicant, ApplicantStatus, utcnow

logger = logging.getLogger(__name__)


def upgrade_schema(engine: Engine) -> None:
    columns = {column["name"] for column in inspect(engine).get_columns("applicants")}
    if "claimed_at" not in columns:
        _add_claimed_at(engine)


def _add_claimed_at(engine: Engine) -> None:
    table = Applicant.__table__
    column_type = table.c.claimed_at.type.compile(dialect=engine.dialect)
    # PostgreSQL may see several replicas start at once; SQLite has no IF NOT EXISTS here.
    guard = "IF NOT EXISTS " if engine.dialect.name == "postgresql" else ""
    with engine.begin() as connection:
        connection.execute(
            text(f"ALTER TABLE applicants ADD COLUMN {guard}claimed_at {column_type}")
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_applicants_claimed_at ON applicants (claimed_at)")
        )
        # Claims made before the upgrade have no timestamp: their expiry clock starts now.
        result = connection.execute(
            update(table)
            .where(
                table.c.status == ApplicantStatus.UNDER_REVIEW,
                table.c.assigned_reviewer != "",
                table.c.claimed_at.is_(None),
            )
            .values(claimed_at=utcnow())
        )
    logger.info("added applicants.claimed_at, stamped %s existing claims", result.rowcount)
