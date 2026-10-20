"""Bangladesh National ID number validation.

Three formats are in circulation:

* 10 digits - smart card NID (2016 onwards)
* 13 digits - legacy NID
* 17 digits - legacy NID prefixed with the 4-digit birth year

The 17-digit form therefore encodes the birth year, which the DOB check uses.
"""

from __future__ import annotations

import re
from datetime import date

from .base import CheckContext, CheckOutcome, register

VALID_LENGTHS = (10, 13, 17)
_DIGITS = re.compile(r"^\d+$")


def normalize_nid(value: str) -> str:
    return re.sub(r"[\s-]", "", value or "")


def validate_nid(value: str) -> tuple[bool, str]:
    """Return (is_valid, reason)."""
    nid = normalize_nid(value)
    if not nid:
        return False, "empty"
    if not _DIGITS.match(nid):
        return False, "non_digit_characters"
    if len(nid) not in VALID_LENGTHS:
        return False, f"length_{len(nid)}_not_in_10_13_17"
    if len(set(nid)) == 1:
        return False, "repeated_single_digit"
    if len(nid) == 17:
        year = int(nid[:4])
        if not 1900 <= year <= date.today().year:
            return False, "implausible_birth_year_prefix"
    return True, "ok"


def birth_year_from_nid(value: str) -> int | None:
    nid = normalize_nid(value)
    return int(nid[:4]) if len(nid) == 17 and nid.isdigit() else None


@register("nid_format")
def check_nid_format(ctx: CheckContext) -> CheckOutcome:
    ok, reason = validate_nid(ctx.applicant.nid_number)
    return CheckOutcome(
        name="nid_format",
        passed=ok,
        risk_points=0 if ok else 40,
        details={"reason": reason, "length": len(normalize_nid(ctx.applicant.nid_number))},
    )
