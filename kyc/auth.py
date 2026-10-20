"""API-key authentication with three roles.

* ``agent``    - the onboarding channel (mobile app backend / branch tablet): creates
                 applicants, uploads documents, submits.
* ``reviewer`` - compliance officer: works the queue and records decisions.
* ``admin``    - everything, plus re-running checks and stats.

Keys come from ``API_KEYS`` ("key:role:name" pairs). This keeps the service
self-contained; in production the dependency would validate a JWT from the
identity provider instead and map its claims onto the same ``Principal``.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header

from .config import Settings, get_settings
from .errors import PermissionDenied

ROLE_RANK = {"agent": 1, "reviewer": 2, "admin": 3}


@dataclass(frozen=True)
class Principal:
    name: str
    role: str

    def has_role(self, *roles: str) -> bool:
        return self.role in roles or self.role == "admin"


def get_principal(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings),
) -> Principal:
    if not x_api_key:
        raise PermissionDenied("Missing X-API-Key header.")
    entry = settings.parsed_api_keys.get(x_api_key)
    if entry is None:
        raise PermissionDenied("Invalid API key.")
    return Principal(name=entry.name, role=entry.role)


def require_role(*roles: str):
    def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.has_role(*roles):
            raise PermissionDenied(
                f"Role '{principal.role}' may not perform this action.",
                details={"required_any_of": list(roles)},
            )
        return principal

    return dependency
