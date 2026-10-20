"""Document intake: type sniffing, size limits, hashing and storage."""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Principal
from ..config import Settings
from ..errors import InvalidTransitionError, NotFoundError, UploadRejected
from ..models import Applicant, Document, DocumentKind
from ..storage import get_storage
from ..workflow import EDITABLE_STATES

_MAGIC = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "application/pdf": (b"%PDF-",),
}
_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "application/pdf": "pdf"}


def sniff_content_type(data: bytes) -> str | None:
    for content_type, signatures in _MAGIC.items():
        if any(data.startswith(sig) for sig in signatures):
            return content_type
    return None


def validate_upload(data: bytes, declared_type: str, kind: DocumentKind, settings: Settings) -> str:
    """Return the verified content type or raise ``UploadRejected``."""
    if not data:
        raise UploadRejected("Uploaded file is empty.")
    if len(data) > settings.max_upload_bytes:
        raise UploadRejected(
            f"File exceeds the {settings.max_upload_bytes // 1024 // 1024} MB limit.",
            details={"size_bytes": len(data), "max_bytes": settings.max_upload_bytes},
        )
    sniffed = sniff_content_type(data)
    if sniffed is None or sniffed not in settings.allowed_content_types:
        raise UploadRejected(
            "Unsupported file type; upload a JPEG, PNG or PDF.",
            details={"declared": declared_type, "detected": sniffed},
        )
    if declared_type and declared_type.split(";")[0].strip() != sniffed:
        raise UploadRejected(
            "Declared content type does not match the file contents.",
            details={"declared": declared_type, "detected": sniffed},
        )
    if kind == DocumentKind.SELFIE and sniffed == "application/pdf":
        raise UploadRejected("A selfie must be an image, not a PDF.")
    return sniffed


def store_document(
    db: Session,
    applicant: Applicant,
    *,
    kind: DocumentKind,
    data: bytes,
    filename: str,
    declared_type: str,
    actor: Principal,
    settings: Settings,
) -> Document:
    if applicant.status not in EDITABLE_STATES:
        raise InvalidTransitionError(
            f"Documents cannot be changed while applicant is '{applicant.status.value}'."
        )
    content_type = validate_upload(data, declared_type, kind, settings)
    digest = hashlib.sha256(data).hexdigest()
    safe_name = PurePosixPath(filename or "upload").name
    key = f"applicants/{applicant.id}/{kind.value}-{digest[:16]}.{_EXTENSIONS[content_type]}"
    get_storage().save(key, data, content_type)

    # Replace a previous upload of the same kind (re-submission after NEEDS_MORE_INFO).
    previous = applicant.document_of(kind)
    if previous is not None:
        if previous.storage_key != key:
            get_storage().delete(previous.storage_key)
        db.delete(previous)
        db.flush()

    document = Document(
        applicant_id=applicant.id,
        kind=kind,
        original_filename=safe_name[:255],
        content_type=content_type,
        size_bytes=len(data),
        sha256=digest,
        storage_key=key,
        uploaded_by=actor.name,
    )
    db.add(document)
    db.commit()
    db.refresh(applicant)
    return document


def find_duplicates(db: Session, document: Document) -> list[Document]:
    return list(
        db.execute(
            select(Document).where(
                Document.sha256 == document.sha256, Document.applicant_id != document.applicant_id
            )
        ).scalars()
    )


def get_document(db: Session, document_id: str) -> Document:
    document = db.get(Document, document_id)
    if document is None:
        raise NotFoundError(f"Document '{document_id}' not found.")
    return document


def read_content(document: Document) -> bytes:
    try:
        return get_storage().load(document.storage_key)
    except FileNotFoundError:
        raise NotFoundError("Stored file is missing.")
