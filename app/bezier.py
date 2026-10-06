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

Every adjudication (peak selection, limit comparison, inter-segment velocity
continuity) is performed on exact non-negative ratios via cross multiplication
of decimal integers, so no finite-precision division can ever erase a genuine
exceedance -- even one hiding at the 60th decimal place -- while a value
algebraically equal to the limit is still recognized as exactly equal.
Rounded decimal values are only produced for reporting.
"""

from dataclasses import dataclass
from decimal import Decimal, localcontext


def _exact_product(x: Decimal, y: Decimal) -> Decimal:
    """Multiply two Decimals without any rounding.

    The product of an n-digit and an m-digit integer mantissa has at most
    n + m significant digits, so a context sized accordingly makes decimal
    integer multiplication exact regardless of the caller's precision.
    """
    with localcontext() as ctx:
        ctx.prec = max(2, len(x.as_tuple().digits) + len(y.as_tuple().digits))
        return x * y


def _exact_add(x: Decimal, y: Decimal) -> Decimal:
    """Add two Decimals without any rounding.

    Precision is sized to the digit span between the most and least
    significant places of both operands (plus a carry), so values separated
    by dozens of decimal orders (e.g. 1 and 1e-60) keep every digit.
    """
    high = max(x.adjusted(), y.adjusted())
    low = min(x.as_tuple().exponent, y.as_tuple().exponent)
    with localcontext() as ctx:
        ctx.prec = max(2, high - low + 2)
        return x + y


def _exact_diff(x: Decimal, y: Decimal) -> Decimal:
    """Subtract two Decimals without any rounding.

    The negation happens *inside* the raised-precision context: a bare
    ``-y`` outside it would round at the caller's (possibly 28-digit)
    precision and erase trailing digits before the subtraction ever runs.
    """
    high = max(x.adjusted(), y.adjusted())
    low = min(x.as_tuple().exponent, y.as_tuple().exponent)
    with localcontext() as ctx:
        ctx.prec = max(2, high - low + 2)
        return x - y


def _exact_abs(x: Decimal) -> Decimal:
    """Absolute value without rounding at the caller's context precision."""
    with localcontext() as ctx:
        ctx.prec = max(2, len(x.as_tuple().digits))
        return abs(x)


def _span_precision(*values: Decimal, extra: int = 10) -> int:
    """Enough digits to hold every place occupied by ``values`` exactly."""
    if not values:
        return max(2, extra)
    high = max(v.adjusted() for v in values)
    low = min(v.as_tuple().exponent for v in values)
    return max(2, high - low + 2, extra)


@dataclass(frozen=True)
class Rational:
    """A non-negative exact ratio ``numerator / denominator``.

    ``denominator`` is always strictly positive, so two ratios (and a ratio
    against a positive scalar limit) compare exactly by cross multiplication.
    """

    numerator: Decimal
    denominator: Decimal

    def cross_gt(self, other_numerator: Decimal, other_denominator: Decimal) -> bool:
        """True iff self > other_numerator/other_denominator (exact)."""
        left = _exact_product(self.numerator, other_denominator)
        right = _exact_product(other_numerator, self.denominator)
        return left > right

    def exceeds(self, limit: Decimal) -> bool:
        """True iff this ratio is strictly greater than ``limit`` (exact)."""
        return self.numerator > _exact_product(limit, self.denominator)

    def to_decimal(self, extra_digits: int = 40) -> Decimal:
        """Rounded view of the ratio, used only for human/JSON reporting.

        Precision is derived from the operands' own digit span (so trailing
        digits of a sparse value such as 1e60 + 1 survive), plus
        ``extra_digits`` for genuinely non-terminating quotients (e.g. 1/3).
        """
        n = self.numerator
        if n == 0:
            return Decimal(0)
        span = n.adjusted() - n.as_tuple().exponent + 2
        denom_digits = len(self.denominator.as_tuple().digits)
        with localcontext() as ctx:
            ctx.prec = max(span, denom_digits, 2) + max(extra_digits, 2)
            return n / self.denominator


@dataclass(frozen=True)
class PeakResult:
    max_speed: Rational
    max_accel: Rational


def _max_rational(values: list[Rational]) -> Rational:
    best = values[0]
    for candidate in values[1:]:
        if candidate.cross_gt(best.numerator, best.denominator):
            best = candidate
    return best


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

    The products are evaluated with enough digits for their exact integer
    mantissas, preserving differences that live dozens of decimal places in.
    """
    _, _, pa2, pa3 = points_a
    pb0, pb1, _, _ = points_b
    left = _exact_product(_exact_diff(pa3, pa2), duration_b)
    right = _exact_product(_exact_diff(pb1, pb0), duration_a)
    return left == right


def decimal_endpoint_velocity(
    duration: Decimal, points: tuple[Decimal, ...], end: bool, precision: int = 80
) -> Decimal:
    """Endpoint velocity 3(P1-P0)/T (or 3(P3-P2)/T) for diagnostic messages."""
    with localcontext() as ctx:
        ctx.prec = _span_precision(*points, duration, extra=precision)
        three = Decimal(3)
        if end:
            return _exact_product(three, _exact_diff(points[3], points[2])) / duration
        return _exact_product(three, _exact_diff(points[1], points[0])) / duration


def decimal_segment_peaks(
    duration: Decimal, points: tuple[Decimal, Decimal, Decimal, Decimal]
) -> PeakResult:
    """Maximum speed and acceleration magnitude over tau in [0, 1].

    All candidate magnitudes are kept as exact ratios; selecting the peak and
    comparing it with a limit never involves a rounded division.
    """
    p0, p1, p2, p3 = points

    # Every difference, product and absolute value below goes through an
    # _exact_* helper that sizes its own decimal context to the operands' full
    # digit span, so a trailing 60th-decimal digit can never be rounded away
    # (a surrounding default-precision context would clip even a plain abs()).

    two = Decimal(2)
    three = Decimal(3)
    four = Decimal(4)
    six = Decimal(6)

    d10 = _exact_diff(p1, p0)
    d21 = _exact_diff(p2, p1)
    d32 = _exact_diff(p3, p2)

    # Velocity (scaled by 3/T) is the quadratic q(tau) = a*tau^2 + b*tau + c,
    # obtained by expanding the Bernstein form of p'(tau)/3.
    c = d10
    b = _exact_product(two, _exact_diff(d21, d10))
    a = _exact_add(_exact_diff(d10, _exact_product(two, d21)), d32)

    # Endpoint speeds: 3*|q(0)|/T and 3*|q(1)|/T.
    speed_candidates = [
        Rational(_exact_product(three, _exact_abs(c)), duration),
        Rational(
            _exact_product(three, _exact_abs(_exact_add(_exact_add(a, b), c))),
            duration,
        ),
    ]

    # Interior extremum at tau* = -b/(2a), strictly inside (0, 1):
    #   tau* > 0  <=>  a*b < 0
    #   tau* < 1  <=>  a*(2a + b) > 0
    # Both sign tests avoid division.  At the vertex
    #   q(tau*) = (4ac - b^2)/(4a),
    # so the speed there is 3*|4ac-b^2|/(4*|a|*T) -- an exact ratio.
    if a != 0:
        ab = _exact_product(a, b)
        a2ab = _exact_product(a, _exact_add(_exact_product(two, a), b))
        if ab < 0 < a2ab:
            numerator = _exact_product(
                three,
                _exact_abs(_exact_diff(_exact_product(four, _exact_product(a, c)),
                                       _exact_product(b, b))),
            )
            denominator = _exact_product(four, _exact_product(_exact_abs(a), duration))
            speed_candidates.append(Rational(numerator, denominator))
    # a == 0: q is linear, extrema are the endpoints already covered.

    # Acceleration is affine in tau; its magnitude peak lies at an endpoint:
    # 6*|p0 - 2p1 + p2|/T^2 and 6*|p1 - 2p2 + p3|/T^2.
    duration_sq = _exact_product(duration, duration)
    a0 = _exact_add(_exact_diff(p0, _exact_product(two, p1)), p2)
    a1 = _exact_add(_exact_diff(p1, _exact_product(two, p2)), p3)
    accel_candidates = [
        Rational(_exact_product(six, _exact_abs(a0)), duration_sq),
        Rational(_exact_product(six, _exact_abs(a1)), duration_sq),
    ]

    return PeakResult(
        max_speed=_max_rational(speed_candidates),
        max_accel=_max_rational(accel_candidates),
    )
