"""Semantic validation and continuous-curve auditing.

All adjudication is exact: the parsed decimal inputs are exact rationals, and
:mod:`app.bezier` evaluates peaks, limit comparisons and boundary continuity
on :class:`fractions.Fraction`.  A speed strictly over the cap -- even by a
single unit in the 60th decimal place -- is therefore a violation, and
adjacent endpoint velocities are continuous only when mathematically equal.

Rendering back to JSON decimals is the sole step allowed to round, and it
adapts its precision so a reported peak/violation can never display the same
number as a strictly smaller limit.
"""

from fractions import Fraction

from fastapi.exceptions import RequestValidationError

from .bezier import (
    decimal_endpoint_positions,
    decimal_endpoint_velocities_equal,
    decimal_endpoint_velocity,
    decimal_segment_peaks,
)
from .decimalio import (
    fraction_to_decimal,
    max_significant_digits,
    render_strictly_greater,
)
from .schemas import AuditRequest

_CONSTRAINT_VELOCITY = "velocity"
_CONSTRAINT_ACCELERATION = "acceleration"


def _err(loc: list, msg: str, err_type: str = "value_error") -> dict:
    return {"loc": loc, "msg": msg, "type": err_type}


def _input_numbers(req: AuditRequest):
    """Every decimal supplied on the request (controls render precision)."""
    for joint in req.joints:
        yield joint.lower
        yield joint.upper
        yield joint.maxVelocity
        yield joint.maxAcceleration
    for seg in req.segments:
        yield seg.duration
        for group in seg.controlPoints:
            yield from group


def audit_trajectory(req: AuditRequest) -> dict:
    errors: list[dict] = []

    # The response must stay faithful to every digit supplied on the request;
    # at minimum 60 significant digits are used.
    render_precision = max_significant_digits(_input_numbers(req))

    n_joints = len(req.joints)
    for j, joint in enumerate(req.joints):
        if joint.upper < joint.lower:
            errors.append(
                _err(
                    ["joints", j, "upper"],
                    "upper bound must be greater than or equal to lower bound",
                )
            )
        if joint.maxVelocity <= 0:
            errors.append(
                _err(["joints", j, "maxVelocity"], "maxVelocity must be positive")
            )
        if joint.maxAcceleration <= 0:
            errors.append(
                _err(
                    ["joints", j, "maxAcceleration"],
                    "maxAcceleration must be positive",
                )
            )

    durations: list = []
    # points[s][j] = (P0, P1, P2, P3) as Decimals (exact rationals)
    points: list[list[tuple]] = []

    for s, seg in enumerate(req.segments):
        if seg.duration <= 0:
            errors.append(
                _err(
                    ["segments", s, "duration"],
                    "segment duration must be strictly positive",
                )
            )
        durations.append(seg.duration)

        if len(seg.controlPoints) != n_joints:
            errors.append(
                _err(
                    ["segments", s, "controlPoints"],
                    f"expected {n_joints} joint control-point groups, "
                    f"got {len(seg.controlPoints)}",
                    err_type="value_error.count",
                )
            )
            points.append([])
            continue

        seg_points: list[tuple] = []
        malformed = False
        for j, cps in enumerate(seg.controlPoints):
            if len(cps) != 4:
                errors.append(
                    _err(
                        ["segments", s, "controlPoints", j],
                        f"each joint needs exactly 4 control positions, got {len(cps)}",
                        err_type="value_error.length",
                    )
                )
                malformed = True
                continue
            seg_points.append((cps[0], cps[1], cps[2], cps[3]))
        points.append([] if malformed else seg_points)

    # Closed-travel checks (every control position, not only endpoints).
    for s, seg_points in enumerate(points):
        if not seg_points:
            continue
        for j, cps in enumerate(seg_points):
            joint = req.joints[j]
            for k, pos in enumerate(cps):
                if pos < joint.lower or pos > joint.upper:
                    errors.append(
                        _err(
                            ["segments", s, "controlPoints", j, k],
                            f"control position {pos} is outside the closed travel "
                            f"[{joint.lower}, {joint.upper}]",
                        )
                    )

    # Exact endpoint continuity between adjacent segments.
    for s in range(len(req.segments) - 1):
        cur, nxt = points[s], points[s + 1]
        if not cur or not nxt:
            continue
        for j in range(n_joints):
            _, p_end = decimal_endpoint_positions(cur[j])
            q_start, _ = decimal_endpoint_positions(nxt[j])
            if p_end != q_start:
                errors.append(
                    _err(
                        ["segments", s + 1, "controlPoints", j, 0],
                        f"position discontinuity at the segment boundary: "
                        f"{p_end} != {q_start}",
                        err_type="continuity.position",
                    )
                )
            if not decimal_endpoint_velocities_equal(
                durations[s], cur[j], durations[s + 1], nxt[j]
            ):
                v_end = decimal_endpoint_velocity(durations[s], cur[j], end=True)
                v_start = decimal_endpoint_velocity(
                    durations[s + 1], nxt[j], end=False
                )
                errors.append(
                    _err(
                        ["segments", s + 1, "controlPoints", j],
                        f"velocity discontinuity at the segment boundary: "
                        f"{fraction_to_decimal(v_end, render_precision)} != "
                        f"{fraction_to_decimal(v_start, render_precision)}",
                        err_type="continuity.velocity",
                    )
                )

    if errors:
        raise RequestValidationError(errors)

    # --- Continuous adjudication over every segment and joint -------------
    violations: list[dict] = []
    peaks_out: list[dict] = []

    for j, joint in enumerate(req.joints):
        speed_limit = Fraction(joint.maxVelocity)
        accel_limit = Fraction(joint.maxAcceleration)

        joint_speed = Fraction(0)
        joint_accel = Fraction(0)
        joint_violations: list[dict] = []
        for s in range(len(req.segments)):
            peaks = decimal_segment_peaks(durations[s], points[s][j])
            speed = peaks.max_speed
            accel = peaks.max_accel

            if speed > joint_speed:
                joint_speed = speed
            if accel > joint_accel:
                joint_accel = accel

            # Strictly over the limit only; exact equality is approved, with
            # no rounding epsilon that could pardon a genuine exceedance.
            if speed > speed_limit:
                value, limit = render_strictly_greater(
                    speed, speed_limit, render_precision
                )
                joint_violations.append(
                    {
                        "segment": s + 1,
                        "joint": j + 1,
                        "constraint": _CONSTRAINT_VELOCITY,
                        "value": value,
                        "limit": limit,
                    }
                )
            if accel > accel_limit:
                value, limit = render_strictly_greater(
                    accel, accel_limit, render_precision
                )
                joint_violations.append(
                    {
                        "segment": s + 1,
                        "joint": j + 1,
                        "constraint": _CONSTRAINT_ACCELERATION,
                        "value": value,
                        "limit": limit,
                    }
                )

        if any(v["constraint"] == _CONSTRAINT_VELOCITY for v in joint_violations):
            peak_velocity, _ = render_strictly_greater(
                joint_speed, speed_limit, render_precision
            )
        else:
            peak_velocity = fraction_to_decimal(joint_speed, render_precision)
        if any(v["constraint"] == _CONSTRAINT_ACCELERATION for v in joint_violations):
            peak_acceleration, _ = render_strictly_greater(
                joint_accel, accel_limit, render_precision
            )
        else:
            peak_acceleration = fraction_to_decimal(joint_accel, render_precision)

        violations.extend(joint_violations)
        peaks_out.append(
            {
                "joint": j + 1,
                "maxVelocity": peak_velocity,
                "maxAcceleration": peak_acceleration,
            }
        )

    # Stable ordering: segment number, joint number, constraint type.
    type_order = {_CONSTRAINT_VELOCITY: 0, _CONSTRAINT_ACCELERATION: 1}
    violations.sort(key=lambda v: (v["segment"], v["joint"], type_order[v["constraint"]]))

    return {
        "approved": len(violations) == 0,
        "peaks": peaks_out,
        "violations": violations,
    }
