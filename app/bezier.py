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
Every input is a finite decimal, hence an exact rational; all adjudication
(peak evaluation, limit comparisons, boundary continuity cross products) is
performed with :class:`~fractions.Fraction`, so no working precision can ever
round a genuine -- however tiny -- exceedance or discontinuity away.  Only the
final human-facing rendering needs decimal rounding.
"""

from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True)
class PeakResult:
    max_speed: Fraction
    max_accel: Fraction


def decimal_endpoint_positions(
    points: tuple,
) -> tuple:
    return points[0], points[3]


def decimal_endpoint_velocities_equal(
    duration_a,
    points_a: tuple,
    duration_b,
    points_b: tuple,
) -> bool:
    """Exact equality of the end velocity of segment A and start of segment B.

    Compares cross products so no division is involved at all:
    3(P3-P2)/T_a == 3(Q1-Q0)/T_b  <=>  (P3-P2)*T_b == (Q1-Q0)*T_a.
    Both sides are evaluated as exact rationals, independent of any Decimal
    context precision.
    """
    _, _, pa2, pa3 = points_a
    pb0, pb1, _, _ = points_b
    return (Fraction(pa3) - Fraction(pa2)) * Fraction(duration_b) == (
        Fraction(pb1) - Fraction(pb0)
    ) * Fraction(duration_a)


def decimal_endpoint_velocity(duration, points: tuple, end: bool) -> Fraction:
    """Endpoint velocity 3(P1-P0)/T (or 3(P3-P2)/T), exactly."""
    if end:
        return Fraction(3) * (Fraction(points[3]) - Fraction(points[2])) / Fraction(duration)
    return Fraction(3) * (Fraction(points[1]) - Fraction(points[0])) / Fraction(duration)


def decimal_segment_peaks(duration, points: tuple) -> PeakResult:
    """Maximum speed and acceleration magnitude over tau in [0, 1], exact."""
    p0, p1, p2, p3 = (Fraction(p) for p in points)
    total_time = Fraction(duration)

    d10 = p1 - p0
    d21 = p2 - p1
    d32 = p3 - p2

    # Velocity (scaled by 3/T) is the quadratic q(tau) = a*tau^2 + b*tau + c,
    # obtained by expanding the Bernstein form of p'(tau)/3.
    c = d10
    b = 2 * (d21 - d10)
    a = d10 - 2 * d21 + d32

    speed_candidates = [abs(c), abs(a + b + c)]  # tau = 0 and tau = 1

    if a != 0:
        # Vertex tau* = -b/(2a); q(tau*) = c - b^2/(4a).  Both are rational,
        # so the interior extremum participates in adjudication exactly.
        tau_star = -b / (2 * a)
        if 0 < tau_star < 1:
            q_star = c - (b * b) / (4 * a)
            speed_candidates.append(abs(q_star))
    # a == 0: q is linear, extrema are the endpoints already covered.

    max_speed = 3 * max(speed_candidates) / total_time

    # Acceleration is affine in tau; its magnitude peak lies at an endpoint.
    a0 = p0 - 2 * p1 + p2
    a1 = p1 - 2 * p2 + p3
    max_accel = 6 * max(abs(a0), abs(a1)) / (total_time * total_time)

    return PeakResult(max_speed=max_speed, max_accel=max_accel)
