"""Face match between the selfie and the NID front photo.

``FaceMatcher`` is a protocol so a real biometric vendor can be plugged in.
``StubFaceMatcher`` is deterministic and dependency-free: it returns a configurable
similarity, except when the applicant uploaded the very same file as both selfie
and NID front, which is a classic shortcut attempt and scores zero.
"""

from __future__ import annotations

import hashlib
from typing import Protocol

from ..models import DocumentKind
from .base import CheckContext, CheckOutcome, register


class FaceMatcher(Protocol):
    def similarity(self, selfie: bytes, id_photo: bytes) -> float: ...


class StubFaceMatcher:
    def __init__(self, default_similarity: float = 0.93) -> None:
        self.default_similarity = default_similarity

    def similarity(self, selfie: bytes, id_photo: bytes) -> float:
        if not selfie or not id_photo:
            return 0.0
        if hashlib.sha256(selfie).digest() == hashlib.sha256(id_photo).digest():
            return 0.0
        return self.default_similarity


_matcher: FaceMatcher = StubFaceMatcher()


def set_face_matcher(matcher: FaceMatcher) -> None:
    global _matcher
    _matcher = matcher


def get_face_matcher() -> FaceMatcher:
    return _matcher


@register("face_match")
def check_face_match(ctx: CheckContext) -> CheckOutcome:
    selfie = ctx.documents.get(DocumentKind.SELFIE.value, b"")
    id_photo = ctx.documents.get(DocumentKind.NID_FRONT.value, b"")
    if not selfie or not id_photo:
        return CheckOutcome(
            "face_match",
            passed=False,
            risk_points=30,
            details={"reason": "missing_document", "similarity": None},
        )
    score = get_face_matcher().similarity(selfie, id_photo)
    identical = hashlib.sha256(selfie).digest() == hashlib.sha256(id_photo).digest()
    passed = score >= ctx.settings.face_match_threshold and not identical
    return CheckOutcome(
        name="face_match",
        passed=passed,
        risk_points=0 if passed else 45,
        details={
            "similarity": round(score, 4),
            "threshold": ctx.settings.face_match_threshold,
            "identical_files": identical,
        },
    )
