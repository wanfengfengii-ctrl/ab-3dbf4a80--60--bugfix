"""API smoke tests against a running server.

Exercises both an approved and a violating trajectory over the real HTTP
interface, plus 422 handling and decimal-equivalence invariance.  Exits
non-zero on any failure.
"""

import json
import os
import sys
from decimal import Decimal, localcontext

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

        # 6. A velocity exceeding the cap only at the 60th decimal place must
        #    still be rejected, with peak/violation shown distinctly from the
        #    smaller limit (regression: used to collapse onto 1.0).
        b = "1.000000000000000000000000000000000000000000000000000000000001"
        with localcontext() as ctx:
            ctx.prec = 100
            bd = Decimal(b)
            b2, b3 = str(2 * bd), str(3 * bd)
        tiny_over = {
            "joints": [
                {"lower": "0", "upper": b3, "maxVelocity": "1", "maxAcceleration": "1"}
            ],
            "segments": [{"duration": "3", "controlPoints": [["0", b, b2, b3]]}],
        }
        r = post(client, tiny_over)
        ok = r.status_code == 200
        check("60th-decimal exceedance -> 200", ok, r.text)
        if ok:
            body = r.json()
            violation = {
                "segment": 1,
                "joint": 1,
                "constraint": "velocity",
                "value": b,
                "limit": 1.0,
            }
            check("60th-decimal exceedance rejected", body["approved"] is False)
            check("60th-decimal violation located", body["violations"] == [violation])
            check(
                "60th-decimal peak distinct from limit",
                body["peaks"][0]["maxVelocity"] == b,
                json.dumps(body["peaks"]),
            )

        # 7. Adjacent segments whose boundary velocities differ only at the
        #    60th decimal must be 422, located at the next segment's first
        #    control point (regression: used to pass as continuous).
        discontinuous = {
            "joints": [
                {"lower": "-2", "upper": "2", "maxVelocity": "10", "maxAcceleration": "10"}
            ],
            "segments": [
                {"duration": "1", "controlPoints": [["-1", "-1", "-1", "0"]]},
                {"duration": "1", "controlPoints": [["0", b, b, b]]},
            ],
        }
        r = post(client, discontinuous)
        check("60th-decimal velocity discontinuity -> 422", r.status_code == 422, r.text)
        if r.status_code == 422:
            locs = [tuple(e["loc"]) for e in r.json()["detail"] if e["type"] == "continuity.velocity"]
            check(
                "velocity discontinuity located at segments[1].controlPoints[0]",
                ("segments", 1, "controlPoints", 0) in locs
                or ("body", "segments", 1, "controlPoints", 0) in locs,
                str(locs),
            )

    print()
    if failures:
        print(f"SMOKE FAILED: {len(failures)} check(s) failed: {failures}", file=sys.stderr)
        return 1
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
