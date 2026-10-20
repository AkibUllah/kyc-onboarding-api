from __future__ import annotations

from datetime import date

from .base import CheckContext, CheckOutcome, register
from .nid import birth_year_from_nid


def age_on(dob: date, today: date) -> int:
    years = today.year - dob.year
    if (today.month, today.day) < (dob.month, dob.day):
        years -= 1
    return years


@register("dob_consistency")
def check_dob_consistency(ctx: CheckContext) -> CheckOutcome:
    """Applicant must be an adult, and a 17-digit NID must agree with the stated birth year."""
    dob = ctx.applicant.date_of_birth
    today = date.today()
    problems: list[str] = []
    age = age_on(dob, today)
    if dob > today:
        problems.append("dob_in_future")
    elif age < ctx.settings.min_applicant_age:
        problems.append(f"under_{ctx.settings.min_applicant_age}")
    if age > 120:
        problems.append("implausible_age")
    nid_year = birth_year_from_nid(ctx.applicant.nid_number)
    if nid_year is not None and nid_year != dob.year:
        problems.append("nid_year_mismatch")
    return CheckOutcome(
        name="dob_consistency",
        passed=not problems,
        risk_points=0 if not problems else (35 if "nid_year_mismatch" in problems else 50),
        details={"age": age, "nid_birth_year": nid_year, "problems": problems},
    )
