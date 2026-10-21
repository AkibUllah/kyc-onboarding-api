from __future__ import annotations

import pytest

from kyc.config import Settings


@pytest.mark.parametrize(
    "given",
    [
        "postgres://kyc:secret@db.render.internal:5432/kyc",
        "postgresql://kyc:secret@db.render.internal:5432/kyc",
    ],
)
def test_managed_postgres_urls_are_rewritten_to_psycopg3(given: str) -> None:
    settings = Settings(_env_file=None, database_url=given)
    assert settings.database_url == "postgresql+psycopg://kyc:secret@db.render.internal:5432/kyc"


@pytest.mark.parametrize(
    "given",
    ["sqlite://", "sqlite:///./kyc.db", "postgresql+psycopg://kyc:kyc@db:5432/kyc"],
)
def test_explicit_driver_urls_are_left_alone(given: str) -> None:
    assert Settings(_env_file=None, database_url=given).database_url == given
