"""End-to-end tests for POST /api/trajectories/audit."""

import json
from decimal import Decimal

from fastapi.testclient import TestClient


PATH = "/api/trajectories/audit"

# 1 + 1e-60: identical to 1 at IEEE-754 double precision and past the 60th
# significant digit, so any float/60-digit-Decimal pipeline would lose it.
B = "1.000000000000000000000000000000000000000000000000000000000001"
B_DECIMAL = Decimal(B)
# Exact multiples 2B, 3B (control points of a constant-speed B segment).
B2 = "2" + B[1:-1] + "2"
B3 = "3" + B[1:-1] + "3"


def joint(lower=0, upper=10, v=10, a=100):
    return {
        "lower": lower,
        "upper": upper,
        "maxVelocity": v,
        "maxAcceleration": a,
    }


def seg(duration, points):
    return {"duration": duration, "controlPoints": points}


# A smoothstep segment [0,0,1,1] with T=1: endpoint velocities are zero while
# the *interior* speed peaks at 1.5 (at tau=0.5). Endpoint acceleration is 6.
SMOOTHSTEP = seg(1, [[0, 0, 1, 1]])


def test_health(client: TestClient):
    from app.main import HEALTH_PATH

    r = client.get(HEALTH_PATH)
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_valid_passing_trajectory(client: TestClient):
    payload = {"joints": [joint(v=2, a=10)], "segments": [SMOOTHSTEP]}
    r = client.post(PATH, json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["approved"] is True
    assert body["violations"] == []
    assert body["peaks"] == [
        {"joint": 1, "maxVelocity": 1.5, "maxAcceleration": 6.0}
    ]


def test_limit_equality_is_approved(client: TestClient):
    payload = {"joints": [joint(v="1.5", a="6")], "segments": [SMOOTHSTEP]}
    r = client.post(PATH, json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["approved"] is True
    assert r.json()["violations"] == []


def test_interior_speed_violation_is_not_missed(client: TestClient):
    """A peak strictly inside the segment must be caught, not just samples."""
    payload = {"joints": [joint(upper=1, v=1.2, a=10)], "segments": [SMOOTHSTEP]}
    r = client.post(PATH, json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["approved"] is False
    assert body["violations"] == [
        {
            "segment": 1,
            "joint": 1,
            "constraint": "velocity",
            "value": 1.5,
            "limit": 1.2,
        }
    ]


def test_violations_sorted_by_segment_joint_constraint(client: TestClient):
    payload = {
        "joints": [
            joint(upper=10, v=1.0, a=5),    # joint 1: violates both
            joint(upper=10, v=3.0, a=50),   # joint 2: steady speed 3, within limits
        ],
        "segments": [
            seg(1, [[0, 0, 1, 1], [0, 1, 2, 3]]),
            # Position- and velocity-continuous with segment 1:
            seg(1, [[1, 1, 2, 2], [3, 4, 5, 6]]),
        ],
    }
    r = client.post(PATH, json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["approved"] is False
    keys = [(v["segment"], v["joint"], v["constraint"]) for v in body["violations"]]
    assert keys == [
        (1, 1, "velocity"),
        (1, 1, "acceleration"),
        (2, 1, "velocity"),
        (2, 1, "acceleration"),
    ]
    peaks = {(p["joint"]): p for p in body["peaks"]}
    assert peaks[1]["maxVelocity"] == 1.5
    assert peaks[1]["maxAcceleration"] == 6.0
    assert peaks[2]["maxVelocity"] == 3.0
    assert peaks[2]["maxAcceleration"] == 0.0


def test_non_positive_duration_is_422(client: TestClient):
    payload = {"joints": [joint()], "segments": [seg(0, [[0, 0, 1, 1]])]}
    r = client.post(PATH, json=payload)
    assert r.status_code == 422
    locs = [tuple(e["loc"]) for e in r.json()["detail"]]
    assert ("body", "segments", 0, "duration") in locs


def test_negative_duration_is_422(client: TestClient):
    payload = {"joints": [joint()], "segments": [seg("-1.5", [[0, 0, 1, 1]])]}
    r = client.post(PATH, json=payload)
    assert r.status_code == 422
    assert ("body", "segments", 0, "duration") in [
        tuple(e["loc"]) for e in r.json()["detail"]
    ]


def test_position_outside_closed_travel_is_422(client: TestClient):
    payload = {"joints": [joint(lower=0, upper=1)], "segments": [seg(1, [[0, 0, 1, 2]])]}
    r = client.post(PATH, json=payload)
    assert r.status_code == 422
    locs = [tuple(e["loc"]) for e in r.json()["detail"]]
    assert ("body", "segments", 0, "controlPoints", 0, 3) in locs


def test_position_on_travel_boundary_is_allowed(client: TestClient):
    payload = {"joints": [joint(lower=0, upper=1, v=2, a=10)],
               "segments": [seg(1, [[0, 0, 1, 1]])]}
    r = client.post(PATH, json=payload)
    assert r.status_code == 200, r.text


def test_position_discontinuity_is_422(client: TestClient):
    payload = {
        "joints": [joint()],
        "segments": [
            seg(1, [[0, 0, 1, 1]]),
            seg(1, [[1.5, 1.5, 2, 2]]),
        ],
    }
    r = client.post(PATH, json=payload)
    assert r.status_code == 422
    detail = r.json()["detail"]
    match = [
        e for e in detail
        if e["type"] == "continuity.position"
        and tuple(e["loc"]) == ("body", "segments", 1, "controlPoints", 0, 0)
    ]
    assert match


def test_velocity_discontinuity_is_422(client: TestClient):
    # Positions line up at 1, but the next segment starts with nonzero speed.
    payload = {
        "joints": [joint()],
        "segments": [
            seg(1, [[0, 0, 1, 1]]),
            seg(1, [[1, 1.25, 2, 2]]),
        ],
    }
    r = client.post(PATH, json=payload)
    assert r.status_code == 422
    detail = r.json()["detail"]
    match = [
        e for e in detail
        if e["type"] == "continuity.velocity"
        and tuple(e["loc"]) == ("body", "segments", 1, "controlPoints", 0)
    ]
    assert match


def test_velocity_continuity_across_different_durations(client: TestClient):
    # End speed of segment 1: 3*(3-2)/2 = 1.5; start speed of segment 2:
    # 3*(4.5-3)/3 = 1.5 -- equal despite different durations.
    payload = {
        "joints": [joint(upper=10, v=2, a=100)],
        "segments": [
            seg(2, [[0, 1, 2, 3]]),
            seg(3, [[3, 4.5, 6, 7]]),
        ],
    }
    r = client.post(PATH, json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["approved"] is True


def test_non_canonical_decimal_is_422(client: TestClient):
    payload = {"joints": [joint()], "segments": [seg("1e", [[0, 0, 1, 1]])]}
    r = client.post(PATH, json=payload)
    assert r.status_code == 422
    assert ("body", "segments", 0, "duration") in [
        tuple(e["loc"]) for e in r.json()["detail"]
    ]


def test_missing_field_is_422(client: TestClient):
    payload = {"segments": [SMOOTHSTEP]}
    r = client.post(PATH, json=payload)
    assert r.status_code == 422
    assert ("body", "joints") in [tuple(e["loc"]) for e in r.json()["detail"]]


def test_invalid_json_is_422(client: TestClient):
    r = client.post(PATH, content=b"{not json", headers={"content-type": "application/json"})
    assert r.status_code == 422


def test_joint_and_segment_count_limits(client: TestClient):
    too_many_joints = {
        "joints": [joint() for _ in range(9)],
        "segments": [seg(1, [[0, 0, 1, 1]] * 9)],
    }
    assert client.post(PATH, json=too_many_joints).status_code == 422

    no_joints = {"joints": [], "segments": [SMOOTHSTEP]}
    assert client.post(PATH, json=no_joints).status_code == 422

    too_many_segments = {
        "joints": [joint()],
        "segments": [seg(1, [[0, 0, 1, 1]]) for _ in range(201)],
    }
    assert client.post(PATH, json=too_many_segments).status_code == 422


def test_wrong_control_point_count_is_422(client: TestClient):
    payload = {"joints": [joint()], "segments": [seg(1, [[0, 0, 1]])]}
    r = client.post(PATH, json=payload)
    assert r.status_code == 422
    assert ("body", "segments", 0, "controlPoints", 0) in [
        tuple(e["loc"]) for e in r.json()["detail"]
    ]


def test_equivalent_decimal_writings_give_identical_verdict(client: TestClient):
    base = {
        "joints": [joint(lower="0", upper="10", v="1.50", a="6.0")],
        "segments": [seg("1.0", [["0.0", "0", "1", "+1.00"]])],
    }
    variants = [
        {
            "joints": [joint(lower=0, upper=10, v=1.5, a=6)],
            "segments": [seg(1, [[0, 0, 1, 1]])],
        },
        {
            "joints": [joint(lower="0E0", upper="1e1", v="15e-1", a="0.6e1")],
            "segments": [seg("100e-2", [["0.", ".0", "1.000", "1e0"]])],
        },
        {
            "joints": [joint(lower="0.0000", upper="10.000", v="1.50000", a="6.0000")],
            "segments": [seg("1.0000", [["0.00", "0.000", "1.0000", "1"]])],
        },
    ]
    responses = [client.post(PATH, json=v).json() for v in [base, *variants]]
    for body in responses:
        assert body == responses[0]
    assert responses[0]["approved"] is True
    assert responses[0]["peaks"][0]["maxVelocity"] == 1.5
    assert responses[0]["peaks"][0]["maxAcceleration"] == 6.0


# --- Regression: 60th-decimal exceedance must not be pardoned --------------

def test_microscopic_velocity_exceedance_is_rejected(client: TestClient):
    """Constant speed 1+1e-60 against cap 1: strict exceedance is a violation."""
    payload = {
        "joints": [
            {"lower": "0", "upper": "10", "maxVelocity": "1", "maxAcceleration": "1"}
        ],
        "segments": [
            {
                "duration": "3",
                "controlPoints": [["0", B, B2, B3]],
            }
        ],
    }
    r = client.post(PATH, json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["approved"] is False
    assert body["violations"] == [
        {
            "segment": 1,
            "joint": 1,
            "constraint": "velocity",
            "value": 1.0,
            "limit": 1,
        }
    ]
    # The wire carries full precision: parse JSON numbers as Decimal so the
    # tiny excess survives, unlike the float view of the same payload.
    raw = json.loads(r.content, parse_float=Decimal)
    value = raw["violations"][0]["value"]
    limit = raw["violations"][0]["limit"]
    peak = raw["peaks"][0]["maxVelocity"]
    assert value == B_DECIMAL
    assert value > limit
    assert peak == B_DECIMAL
    assert peak > Decimal(1)


def test_microscopic_exceedance_peak_differs_from_limit_on_wire(client: TestClient):
    """The raw JSON literals of peak/limit must not be identical strings."""
    payload = {
        "joints": [
            {"lower": "0", "upper": "10", "maxVelocity": "1", "maxAcceleration": "1"}
        ],
        "segments": [
            {
                "duration": "3",
                "controlPoints": [["0", B, B2, B3]],
            }
        ],
    }
    r = client.post(PATH, json=payload)
    text = r.content.decode()
    assert B in text
    assert '"limit": 1}' in text
    assert '"maxVelocity": %s' % B in text


def test_microscopic_velocity_discontinuity_is_422(client: TestClient):
    """Equal boundary positions but endpoint speeds differing at digit 60."""
    payload = {
        "joints": [
            {"lower": "-2", "upper": "2", "maxVelocity": "10", "maxAcceleration": "10"}
        ],
        "segments": [
            {"duration": "1", "controlPoints": [["-1", "-1", "-1", "0"]]},
            {"duration": "1", "controlPoints": [["0", B, B, B]]},
        ],
    }
    r = client.post(PATH, json=payload)
    assert r.status_code == 422
    match = [
        e
        for e in r.json()["detail"]
        if e["type"] == "continuity.velocity"
        and tuple(e["loc"]) == ("body", "segments", 1, "controlPoints", 0)
    ]
    assert match


def test_microscopic_position_discontinuity_is_422(client: TestClient):
    """Positions equal-looking at double precision but not exactly equal."""
    payload = {
        "joints": [
            {"lower": "0", "upper": "10", "maxVelocity": "10", "maxAcceleration": "10"}
        ],
        "segments": [
            {"duration": "1", "controlPoints": [["0", "0", "0", "1"]]},
            {"duration": "1", "controlPoints": [[B, B, "1", "1"]]},
        ],
    }
    r = client.post(PATH, json=payload)
    assert r.status_code == 422
    match = [
        e
        for e in r.json()["detail"]
        if e["type"] == "continuity.position"
        and tuple(e["loc"]) == ("body", "segments", 1, "controlPoints", 0, 0)
    ]
    assert match


def test_exact_equality_with_many_decimal_digits_is_continuous(client: TestClient):
    """Speeds exactly equal (cross-multiplied) even when inputs carry 61 digits."""
    # Segment 1 end speed: 3*B/1 = 3B.
    # Segment 2 start speed: 3*(2B - B)/1 = 3B -- exactly equal.
    payload = {
        "joints": [
            {"lower": "0", "upper": "10", "maxVelocity": "10", "maxAcceleration": "10"}
        ],
        "segments": [
            {"duration": "1", "controlPoints": [["0", "0", "0", B]]},
            {"duration": "1", "controlPoints": [[B, B2, B2, B2]]},
        ],
    }
    r = client.post(PATH, json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["approved"] is True
