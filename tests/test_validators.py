from datetime import date

import pytest

from kyc.checks.dob import age_on
from kyc.checks.nid import birth_year_from_nid, normalize_nid, validate_nid
from kyc.checks.sanctions import load_list, name_similarity, normalize_name, screen
from kyc.config import get_settings
from kyc.errors import UploadRejected
from kyc.models import DocumentKind
from kyc.services.documents import sniff_content_type, validate_upload
from tests.conftest import JPEG, PDF, PNG


@pytest.mark.parametrize(
    "nid", ["1234567890", "1990123456789", "19901234567890123", "1990 1234 5678 9"]
)
def test_valid_nid_formats(nid):
    assert validate_nid(nid) == (True, "ok")


@pytest.mark.parametrize(
    "nid,reason",
    [
        ("", "empty"),
        ("12345", "length_5_not_in_10_13_17"),
        ("123456789012", "length_12_not_in_10_13_17"),
        ("12345678AB", "non_digit_characters"),
        ("1111111111", "repeated_single_digit"),
        ("18501234567890123", "implausible_birth_year_prefix"),
    ],
)
def test_invalid_nid_formats(nid, reason):
    ok, why = validate_nid(nid)
    assert ok is False and why == reason


def test_nid_birth_year_only_for_17_digits():
    assert birth_year_from_nid("19901234567890123") == 1990
    assert birth_year_from_nid("1990123456789") is None
    assert normalize_nid(" 1990-1234 567 ") == "19901234567"


def test_age_on_handles_birthday_boundaries():
    assert age_on(date(2000, 6, 15), date(2018, 6, 14)) == 17
    assert age_on(date(2000, 6, 15), date(2018, 6, 15)) == 18
    assert age_on(date(2000, 2, 29), date(2024, 2, 28)) == 23


def test_sniff_content_type():
    assert sniff_content_type(JPEG) == "image/jpeg"
    assert sniff_content_type(PNG) == "image/png"
    assert sniff_content_type(PDF) == "application/pdf"
    assert sniff_content_type(b"GIF89a....") is None


def test_validate_upload_rules():
    settings = get_settings()
    assert validate_upload(JPEG, "image/jpeg", DocumentKind.NID_FRONT, settings) == "image/jpeg"
    with pytest.raises(UploadRejected, match="empty"):
        validate_upload(b"", "image/jpeg", DocumentKind.NID_FRONT, settings)
    with pytest.raises(UploadRejected, match="Unsupported"):
        validate_upload(b"GIF89a" + b"\x00" * 10, "image/gif", DocumentKind.NID_FRONT, settings)
    with pytest.raises(UploadRejected, match="does not match"):
        validate_upload(PNG, "image/jpeg", DocumentKind.NID_FRONT, settings)  # spoofed type
    with pytest.raises(UploadRejected, match="selfie must be an image"):
        validate_upload(PDF, "application/pdf", DocumentKind.SELFIE, settings)
    big = JPEG + b"\x00" * settings.max_upload_bytes
    with pytest.raises(UploadRejected, match="limit"):
        validate_upload(big, "image/jpeg", DocumentKind.NID_FRONT, settings)


def test_name_normalisation_and_similarity():
    assert normalize_name("Md. Abdul Karim Chowdhury") == normalize_name("chowdhury abdul KARIM")
    assert name_similarity("Mohammad Faruk Hossain", "Faruk Hossain Md") == 1.0
    assert name_similarity("Rahim Uddin", "Shahida Begum") < 0.5


def test_sanctions_screen_uses_dob_to_confirm_weak_matches():
    entries = load_list(get_settings().sanctions_path)
    assert len(entries) == 7
    exact = screen("Abdul Karim Chowdhury", date(1968, 3, 14), entries)
    assert exact and exact[0].dob_match and exact[0].entry.reference == "SMPL-0001"
    # strong name match without DOB still hits
    assert screen("Karim Chowdhury Abdul", None, entries)
    # weak name variant only hits when DOB agrees
    assert not screen("Abdul Karim Chy", None, entries)
    assert screen("Abdul Karim Chy", date(1968, 3, 14), entries)
    assert screen("Nadia Rahman", date(1995, 1, 1), entries) == []
