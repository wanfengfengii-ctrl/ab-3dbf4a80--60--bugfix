"""Exact continuous-curve adjudication of piecewise cubic Bezier segments.

A segment of duration ``T`` with control positions ``P0..P3`` is

    p(tau)     = (1-tau)^3 P0 + 3(1-tau)^2 tau P1 + 3(1-tau) tau^2 P2 + tau^3 P3
    p'(tau)    = 3[(1-tau)^2 (P1-P0) + 2(1-tau)tau (P2-P1) + tau^2 (P3-P2)]
    p''(tau)   = 6[(1-tau)(P0-2P1+P2) + tau(P1-2P2+P3)]

where tau = t/T in [0, 1].  Physical quantities are v = p'/T, a = p''/T^2.

Because the segment degree is exactly three:

* acceleration is affine in tau, so its magnitude peak is attained at one of
  the segment endpoints -- no interior sampling needed;
* speed is a quadratic in tau, so apart from the endpoints its only possible
  interior extremum is the parabola vertex tau* = -beta/(2 alpha), included
  when it lies strictly inside (0, 1).

This adjudges the *whole* continuous curve instead of control-period samples.
All arithmetic is exact rational :class:`~decimal.Decimal` apart from the
interior-extremum evaluation, which runs at the caller's high context
precision.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class PeakResult:
    max_speed: Decimal
    max_accel: Decimal


def decimal_endpoint_positions(
    points: tuple[Decimal, Decimal, Decimal, Decimal],
) -> tuple[Decimal, Decimal]:
    return points[0], points[3]


def decimal_endpoint_velocities_equal(
    duration_a: Decimal,
    points_a: tuple[Decimal, Decimal, Decimal, Decimal],
    duration_b: Decimal,
    points_b: tuple[Decimal, Decimal, Decimal, Decimal],
) -> bool:
    """Exact equality of the end velocity of segment A and start of segment B.

    Compares cross products so no (rounded) division is involved:
    3(P3-P2)/T_a == 3(Q1-Q0)/T_b  <=>  (P3-P2)*T_b == (Q1-Q0)*T_a.
    """
    _, _, pa2, pa3 = points_a
    pb0, pb1, _, _ = points_b
    return (pa3 - pa2) * duration_b == (pb1 - pb0) * duration_a


def decimal_endpoint_velocity(
    duration: Decimal, points: tuple[Decimal, ...], end: bool
) -> Decimal:
    """Endpoint velocity 3(P1-P0)/T (or 3(P3-P2)/T) for diagnostic messages."""
    if end:
        return Decimal(3) * (points[3] - points[2]) / duration
    return Decimal(3) * (points[1] - points[0]) / duration


def decimal_segment_peaks(
    duration: Decimal, points: tuple[Decimal, Decimal, Decimal, Decimal]
) -> PeakResult:
    """Maximum speed and acceleration magnitude over tau in [0, 1]."""
    p0, p1, p2, p3 = points

    d10 = p1 - p0
    d21 = p2 - p1
    d32 = p3 - p2

    # Velocity (scaled by 3/T) is the quadratic q(tau) = a*tau^2 + b*tau + c,
    # obtained by expanding the Bernstein form of p'(tau)/3.
    c = d10
    b = Decimal(2) * (d21 - d10)
    a = d10 - Decimal(2) * d21 + d32

    velocity_scale = Decimal(3) / duration
    speed_candidates = [abs(c), abs(a + b + c)]  # tau = 0 and tau = 1

    if a != 0:
        tau_star = -b / (Decimal(2) * a)
        if Decimal(0) < tau_star < Decimal(1):
            q_star = a * tau_star * tau_star + b * tau_star + c
            speed_candidates.append(abs(q_star))
    # a == 0: q is linear, extrema are the endpoints already covered.

    max_speed = velocity_scale * max(speed_candidates)

    # Acceleration is affine in tau; its magnitude peak lies at an endpoint.
    accel_scale = Decimal(6) / (duration * duration)
    a0 = p0 - Decimal(2) * p1 + p2
    a1 = p1 - Decimal(2) * p2 + p3
    max_accel = accel_scale * max(abs(a0), abs(a1))

    return PeakResult(max_speed=max_speed, max_accel=max_accel)
