"""Request/response schemas for trajectory audit.

Numeric inputs use :class:`StrictDecimal` so JSON numbers and decimal strings
are both accepted and normalized before any comparison.
"""

from decimal import Decimal

from pydantic import BaseModel, Field

from .decimalio import StrictDecimal


class JointSpec(BaseModel):
    lower: StrictDecimal
    upper: StrictDecimal
    maxVelocity: StrictDecimal
    maxAcceleration: StrictDecimal


class SegmentSpec(BaseModel):
    duration: StrictDecimal
    # One entry per joint; each entry holds the four control positions.
    controlPoints: list[list[StrictDecimal]]


class AuditRequest(BaseModel):
    joints: list[JointSpec] = Field(min_length=1, max_length=8)
    segments: list[SegmentSpec] = Field(min_length=1, max_length=200)


# Re-exported for type checkers that expect Decimal in annotations.
__all__ = ["JointSpec", "SegmentSpec", "AuditRequest", "Decimal"]
