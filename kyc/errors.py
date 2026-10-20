from __future__ import annotations

from typing import Any


class KycError(Exception):
    status_code = 400
    code = "kyc_error"

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class NotFoundError(KycError):
    status_code = 404
    code = "not_found"


class InvalidTransitionError(KycError):
    status_code = 409
    code = "invalid_transition"


class ValidationFailed(KycError):
    status_code = 422
    code = "validation_failed"


class UploadRejected(KycError):
    status_code = 400
    code = "upload_rejected"


class PermissionDenied(KycError):
    status_code = 403
    code = "forbidden"
