from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Applicant


@dataclass
class CheckContext:
    applicant: Applicant
    settings: Settings
    db: Session
    documents: dict[str, bytes] = field(default_factory=dict)  # kind -> raw bytes (lazy loaded)


@dataclass(frozen=True)
class CheckOutcome:
    name: str
    passed: bool
    risk_points: int = 0
    details: dict[str, Any] = field(default_factory=dict)


Check = Callable[[CheckContext], CheckOutcome]

_REGISTRY: dict[str, Check] = {}


def register(name: str) -> Callable[[Check], Check]:
    def decorator(fn: Check) -> Check:
        _REGISTRY[name] = fn
        return fn

    return decorator


def registered_checks() -> dict[str, Check]:
    return dict(_REGISTRY)
