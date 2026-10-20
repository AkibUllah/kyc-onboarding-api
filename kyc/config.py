from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class ApiKey:
    key: str
    role: str
    name: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "kyc-onboarding-api"
    log_level: str = "INFO"
    database_url: str = "sqlite:///./kyc.db"

    storage_backend: str = "local"
    storage_local_dir: Path = Path("./storage")
    s3_bucket: str = ""
    s3_endpoint_url: str = ""

    max_upload_bytes: int = 5 * 1024 * 1024
    allowed_content_types: tuple[str, ...] = ("image/jpeg", "image/png", "application/pdf")

    api_keys: str = Field(
        default="agent-dev-key:agent:Onboarding App,"
        "reviewer-dev-key:reviewer:Nadia Rahman,"
        "admin-dev-key:admin:Ops Admin"
    )

    auto_approve_low_risk: bool = True
    risk_medium_threshold: int = 30
    risk_high_threshold: int = 60
    face_match_threshold: float = 0.80
    min_applicant_age: int = 18
    review_sla_hours: int = 24
    sanctions_csv: str = ""

    @field_validator("storage_backend")
    @classmethod
    def _check_backend(cls, value: str) -> str:
        if value not in {"local", "s3"}:
            raise ValueError("STORAGE_BACKEND must be 'local' or 's3'")
        return value

    @property
    def parsed_api_keys(self) -> dict[str, ApiKey]:
        keys: dict[str, ApiKey] = {}
        for raw in filter(None, (part.strip() for part in self.api_keys.split(","))):
            key, role, name = (raw.split(":", 2) + ["", ""])[:3]
            keys[key] = ApiKey(key=key, role=role or "agent", name=name or role)
        return keys

    @property
    def sanctions_path(self) -> Path:
        return (
            Path(self.sanctions_csv)
            if self.sanctions_csv
            else PACKAGE_DIR / "data" / "sanctions_sample.csv"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
