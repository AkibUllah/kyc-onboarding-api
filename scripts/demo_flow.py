"""Drive a complete onboarding against a running server.

    python scripts/demo_flow.py http://127.0.0.1:8000

Creates two applicants: a clean one that is auto-approved and one whose name is on
the bundled sample sanctions list, which lands in the reviewer queue and is rejected.
"""
from __future__ import annotations

import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
AGENT = {"X-API-Key": "agent-dev-key"}
REVIEWER = {"X-API-Key": "reviewer-dev-key"}

JPEG = b"\xff\xd8\xff\xe0" + bytes(range(256))
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256))


def onboard(client: httpx.Client, payload: dict) -> dict:
    applicant = client.post("/v1/applicants", json=payload, headers=AGENT).raise_for_status().json()
    aid = applicant["id"]
    for kind, data, ctype in (("nid_front", JPEG, "image/jpeg"), ("nid_back", JPEG + b"b", "image/jpeg"),
                              ("selfie", PNG, "image/png")):
        client.post(f"/v1/applicants/{aid}/documents", data={"kind": kind},
                    files={"file": (f"{kind}.img", data, ctype)}, headers=AGENT).raise_for_status()
    client.post(f"/v1/applicants/{aid}/submit", headers=AGENT).raise_for_status()
    return client.get(f"/v1/applicants/{aid}", headers=AGENT).json()


def main() -> None:
    with httpx.Client(base_url=BASE, timeout=10) as client:
        print("health:", client.get("/health").json())

        clean = onboard(client, {
            "full_name": "Rahim Uddin Ahmed", "nid_number": "1990123456789",
            "date_of_birth": "1990-05-14", "phone": "01711223344", "email": "rahim@example.com",
            "address": "House 12, Road 5, Dhanmondi, Dhaka", "occupation": "Shop owner",
            "monthly_income_bdt": 45000,
        })
        print(f"clean applicant -> {clean['status']} (risk {clean['risk_level']}/{clean['risk_score']})")

        risky = onboard(client, {
            "full_name": "Abdul Karim Chowdhury", "nid_number": "19681234567890123",
            "date_of_birth": "1968-03-14", "phone": "01811223344", "address": "Khulshi, Chattogram",
        })
        print(f"risky applicant -> {risky['status']} (risk {risky['risk_level']}/{risky['risk_score']})")

        queue = client.get("/v1/review/queue", headers=REVIEWER).json()
        print(f"review queue: {queue['total']} applicant(s)")
        client.post(f"/v1/review/{risky['id']}/claim", headers=REVIEWER).raise_for_status()
        decided = client.post(f"/v1/review/{risky['id']}/decision",
                              json={"decision": "reject", "reason": "Confirmed sanctions list match"},
                              headers=REVIEWER).raise_for_status().json()
        print(f"decision -> {decided['status']}: {decided['decision_reason']}")
        print("daily stats:", client.get("/v1/stats/daily", headers=REVIEWER).json())


if __name__ == "__main__":
    main()
