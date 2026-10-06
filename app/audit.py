"""Semantic validation and continuous-curve auditing."""

from decimal import Decimal, getcontext

from fastapi.exceptions import RequestValidationError

from .bezier import (
    decimal_endpoint_positions,
    decimal_endpoint_velocities_equal,
    decimal_endpoint_velocity,
    decimal_segment_peaks,
)
from .schemas import AuditRequest

# All segment math (including the interior velocity extremum, the only step
# that can be non-terminating) is carried out at high Decimal precision,
# rather than on control-period samples.
getcontext().prec = 60

# Only a rounding ghost this small is forgiven so that a value mathematically
# equal to a limit is never reported as a violation.  Genuine exceedances are
# vastly larger.
_COMPARE_EPS = Decimal("1e-40")

_CONSTRAINT_VELOCITY = "velocity"
_CONSTRAINT_ACCELERATION = "acceleration"


def _err(loc: list, msg: str, err_type: str = "value_error") -> dict:
    return {"loc": loc, "msg": msg, "type": err_type}


def _exceeds(value: Decimal, limit: Decimal) -> bool:
    """True when value is strictly over the limit (equality passes)."""
    if value <= limit:
        return False
    return (value - limit) > _COMPARE_EPS * max(abs(limit), Decimal(1))


def audit_trajectory(req: AuditRequest) -> dict:
    errors: list[dict] = []

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

    durations: list[Decimal] = []
    # points[s][j] = (P0, P1, P2, P3) as Decimals
    points: list[list[tuple[Decimal, Decimal, Decimal, Decimal]]] = []

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

        seg_points: list[tuple[Decimal, Decimal, Decimal, Decimal]] = []
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
                        f"{v_end} != {v_start}",
                        err_type="continuity.velocity",
                    )
                )

    if errors:
        raise RequestValidationError(errors)

    # --- Continuous adjudication over every segment and joint -------------
    violations: list[dict] = []
    peaks_out: list[dict] = []

    for j, joint in enumerate(req.joints):
        joint_speed = Decimal(0)
        joint_accel = Decimal(0)
        for s in range(len(req.segments)):
            peaks = decimal_segment_peaks(durations[s], points[s][j])
            speed = peaks.max_speed
            accel = peaks.max_accel

            if speed > joint_speed:
                joint_speed = speed
            if accel > joint_accel:
                joint_accel = accel

            if _exceeds(speed, joint.maxVelocity):
                violations.append(
                    {
                        "segment": s + 1,
                        "joint": j + 1,
                        "constraint": _CONSTRAINT_VELOCITY,
                        "value": float(speed),
                        "limit": float(joint.maxVelocity),
                    }
                )
            if _exceeds(accel, joint.maxAcceleration):
                violations.append(
                    {
                        "segment": s + 1,
                        "joint": j + 1,
                        "constraint": _CONSTRAINT_ACCELERATION,
                        "value": float(accel),
                        "limit": float(joint.maxAcceleration),
                    }
                )

        peaks_out.append(
            {
                "joint": j + 1,
                "maxVelocity": float(joint_speed),
                "maxAcceleration": float(joint_accel),
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
