"""Strict canonical-decimal input handling and exact-result rendering.

JSON numbers and decimal strings are both accepted.  Every accepted value is
normalized to a :class:`decimal.Decimal` so that equivalent decimal writings
("1", "1.0", "1.00", "+1.0", "1e0") compare equal exactly.

Adjudication itself runs on exact rationals (:class:`fractions.Fraction`); the
helpers here only render those rationals back to a finite number of decimal
digits for API responses, adapting the precision so a reported value can never
display as equal to a limit it strictly exceeds (no matter how far beyond the
60th decimal place the difference sits).
"""

from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
import re
from typing import Any, Iterable

from typing_extensions import Annotated

from pydantic import BeforeValidator

# Optional sign, then integer/fractional forms, optional exponent.
_DECIMAL_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z")

# Keep values in a range Decimal -> float can represent and that is sane.
_MAX_ADJUSTED_EXPONENT = 1000

# Responses carry at least this many significant digits (matching the service's
# advertised high-precision arithmetic) even when inputs are shorter.
_MIN_RENDER_PRECISION = 60
# Safety stop when a response value needs extra digits to stay distinguishable
# from a strictly smaller limit.
_MAX_RENDER_PRECISION = 2000
_RENDER_PRECISION_STEP = 20


def to_decimal(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean is not a valid decimal number")
    if isinstance(value, int):
        return _check(Decimal(value))
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("value must be a finite number")
        return _check(Decimal(str(value)))
    if isinstance(value, str):
        text = value.strip()
        if not _DECIMAL_RE.match(text):
            raise ValueError(f"value {value!r} is not a canonical decimal number")
        try:
            return _check(Decimal(text))
        except InvalidOperation:
            raise ValueError(f"value {value!r} is not a valid decimal number")
    raise ValueError("value must be a JSON number or a decimal string")


def _check(d: Decimal) -> Decimal:
    if not d.is_finite():
        raise ValueError("value must be a finite number")
    if abs(d.adjusted()) > _MAX_ADJUSTED_EXPONENT:
        raise ValueError("value exponent is out of the supported range")
    return d


StrictDecimal = Annotated[Decimal, BeforeValidator(to_decimal)]


def significant_digits(d: Decimal) -> int:
    """Number of significant digits in an accepted (finite) decimal."""
    return len(d.as_tuple().digits)


def max_significant_digits(values: Iterable[Decimal]) -> int:
    return max(
        _MIN_RENDER_PRECISION,
        max((significant_digits(v) for v in values), default=0),
    )


def round_to_precision(d: Decimal, precision: int) -> Decimal:
    """Round a Decimal to at most ``precision`` significant digits."""
    if d == 0:
        return Decimal(0)
    with localcontext() as ctx:
        ctx.prec = max(precision, 1)
        return +d


def fraction_to_decimal(value: Fraction, precision: int) -> Decimal:
    """Render an exact rational with ``precision`` significant digits."""
    if value == 0:
        return Decimal(0)
    # Extra guard digits during the division, then a single rounding pass.
    with localcontext() as ctx:
        ctx.prec = precision + 10
        d = Decimal(value.numerator) / Decimal(value.denominator)
    return round_to_precision(d, precision)


def render_strictly_greater(
    value: Fraction, smaller: Fraction, precision: int
) -> tuple[Decimal, Decimal]:
    """Render ``value`` and ``smaller`` so the display preserves value > smaller.

    Starts at ``precision`` significant digits and widens it until the rounded
    rendering still shows ``value`` strictly above ``smaller``; an exact
    rational difference is always revealed at finite precision.
    """
    p = max(precision, _MIN_RENDER_PRECISION)
    while True:
        rendered_value = fraction_to_decimal(value, p)
        rendered_smaller = fraction_to_decimal(smaller, p)
        if rendered_value > rendered_smaller or p >= _MAX_RENDER_PRECISION:
            return rendered_value, rendered_smaller
        p += _RENDER_PRECISION_STEP


def decimal_to_literal(d: Decimal) -> str:
    """Plain decimal-notation JSON number literal (never exponent form)."""
    if d == 0:
        return "0"
    text = format(d, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"
