from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..auth import Principal, require_role
from ..config import Settings, get_settings
from ..database import get_db
from ..schemas import DailyStats, SlaStats
from ..services import stats as svc

router = APIRouter(prefix="/v1/stats", tags=["stats"])


@router.get("/daily", response_model=DailyStats)
def daily(
    day: date | None = None,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_role("reviewer", "admin")),
):
    return svc.daily_stats(db, day or datetime.now(timezone.utc).date())


@router.get("/sla", response_model=SlaStats)
def sla(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _: Principal = Depends(require_role("reviewer", "admin")),
):
    return svc.sla_stats(db, settings)
