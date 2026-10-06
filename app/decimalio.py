"""Strict canonical-decimal input handling.

JSON numbers and decimal strings are both accepted.  Every accepted value is
normalized to a :class:`decimal.Decimal` so that equivalent decimal writings
("1", "1.0", "1.00", "+1.0", "1e0") compare equal exactly and convert to the
same Python float later.
"""

from decimal import Decimal, InvalidOperation
import re
from typing import Any

from typing_extensions import Annotated

from pydantic import BeforeValidator

# Optional sign, then integer/fractional forms, optional exponent.
_DECIMAL_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z")

# Keep values in a range Decimal -> float can represent and that is sane.
_MAX_ADJUSTED_EXPONENT = 1000


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


def decimal_to_json(d: Decimal) -> Any:
    """Render a computed Decimal without hiding any significant excess.

    When the value survives a float round trip it is emitted as a JSON number
    (keeping outputs such as ``1.5`` / ``6.0``).  Otherwise its full canonical
    decimal text is emitted as a string, so a value is never displayed as
    identical to a smaller number merely because both round to the same float
    (e.g. a velocity of 1 + 1e-60 versus a limit of 1).

    The float round-trip is checked via the float's shortest round-trip
    repr (``str(f)``) rather than its raw binary value, and without
    :meth:`Decimal.normalize`, which itself rounds at the current context
    precision and could erase the very excess we are trying to surface.
    """
    f = float(d)
    if abs(f) != float("inf") and Decimal(str(f)) == d:
        return f
    return d.to_eng_string()


StrictDecimal = Annotated[Decimal, BeforeValidator(to_decimal)]
