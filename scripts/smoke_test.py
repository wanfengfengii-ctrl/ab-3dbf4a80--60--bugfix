"""API smoke tests against a running server.

Exercises both an approved and a violating trajectory over the real HTTP
interface, plus 422 handling and decimal-equivalence invariance.  Exits
non-zero on any failure.
"""

import json
import os
import sys

import httpx

BASE_URL = os.getenv("API_BASE_URL", "http://api:8000").rstrip("/")
PATH = "/api/trajectories/audit"

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {name}{(' - ' + detail) if detail else ''}")
    if not condition:
        failures.append(name)


def post(client: httpx.Client, payload: dict):
    return client.post(PATH, json=payload)


def main() -> int:
    with httpx.Client(base_url=BASE_URL, timeout=10) as client:
        # 1. Health endpoint.
        health_path = os.getenv("HEALTH_PATH", "/health")
        r = client.get(health_path)
        check("health endpoint 200", r.status_code == 200, r.text)

        # 2. A passing trajectory: smoothstep [0,0,1,1], T=1;
        #    interior speed peak 1.5, peak acceleration 6.
        passing = {
            "joints": [
                {"lower": 0, "upper": 1, "maxVelocity": 2, "maxAcceleration": 10}
            ],
            "segments": [{"duration": 1, "controlPoints": [[0, 0, 1, 1]]}],
        }
        r = post(client, passing)
        ok = r.status_code == 200
        check("passing trajectory -> 200", ok, r.text)
        if ok:
            body = r.json()
            check("passing approved", body["approved"] is True, json.dumps(body))
            check(
                "passing peaks",
                body["peaks"] == [{"joint": 1, "maxVelocity": 1.5, "maxAcceleration": 6.0}],
                json.dumps(body["peaks"]),
            )
            check("passing no violations", body["violations"] == [])

        # 3. A violating trajectory: same curve, tighter velocity cap.
        violating = {
            "joints": [
                {"lower": 0, "upper": 1, "maxVelocity": 1.2, "maxAcceleration": 10}
            ],
            "segments": [{"duration": 1, "controlPoints": [[0, 0, 1, 1]]}],
        }
        r = post(client, violating)
        ok = r.status_code == 200
        check("violating trajectory -> 200", ok, r.text)
        if ok:
            body = r.json()
            check("violating not approved", body["approved"] is False)
            expected = [
                {
                    "segment": 1,
                    "joint": 1,
                    "constraint": "velocity",
                    "value": 1.5,
                    "limit": 1.2,
                }
            ]
            check("violating reports interior speed peak", body["violations"] == expected)

        # 4. 422 for a non-positive duration, with a locatable field path.
        bad = {
            "joints": [
                {"lower": 0, "upper": 1, "maxVelocity": 2, "maxAcceleration": 10}
            ],
            "segments": [{"duration": 0, "controlPoints": [[0, 0, 1, 1]]}],
        }
        r = post(client, bad)
        check("non-positive duration -> 422", r.status_code == 422)
        if r.status_code == 422:
            locs = [tuple(e["loc"]) for e in r.json()["detail"]]
            check(
                "422 locates duration field",
                ("segments", 0, "duration") in locs
                or ("body", "segments", 0, "duration") in locs,
                str(locs),
            )

        # 5. Decimal-equivalence invariance for the same legal trajectory.
        variant_a = {
            "joints": [
                {"lower": "0", "upper": "1", "maxVelocity": "1.50", "maxAcceleration": "6"}
            ],
            "segments": [{"duration": "1.0", "controlPoints": [["+0.0", ".0", "1", "1e0"]]}],
        }
        variant_b = {
            "joints": [
                {"lower": "0E0", "upper": "10e-1", "maxVelocity": "15e-1", "maxAcceleration": "0.6e1"}
            ],
            "segments": [{"duration": "100e-2", "controlPoints": [["0", "0.000", "1.0", "1.0000"]]}],
        }
        ra = post(client, variant_a)
        rb = post(client, variant_b)
        both_ok = ra.status_code == 200 and rb.status_code == 200
        check("equivalent writings -> 200", both_ok, ra.text + rb.text)
        if both_ok:
            ba, bb = ra.json(), rb.json()
            check("equivalent writings identical verdict", ba == bb, json.dumps([ba, bb]))
            check("equivalent writings approved at the limit", ba["approved"] is True)

    print()
    if failures:
        print(f"SMOKE FAILED: {len(failures)} check(s) failed: {failures}", file=sys.stderr)
        return 1
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
