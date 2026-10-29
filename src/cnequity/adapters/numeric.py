"""Numeric parsing helpers shared by source adapters."""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation
from numbers import Integral


def finite_int64(
    value: float | int | str | Decimal,
    *,
    minimum: int = -(2**63),
    maximum: int = 2**63 - 1,
) -> int:
    """Convert a finite integral number or decimal text within signed Int64."""
    if isinstance(value, Integral):
        # A float round trip loses integer precision beyond 2**53 and can
        # move an overflowing value back inside the signed Int64 range.
        numeric = int(value)
    elif isinstance(value, (str, Decimal)):
        try:
            numeric = Decimal(value)
        except InvalidOperation:
            raise ValueError(f"expected a finite integer, got {value!r}") from None
        if not numeric.is_finite() or numeric != numeric.to_integral_value():
            raise ValueError(f"expected a finite integer, got {value!r}")
    else:
        try:
            numeric = float(value)
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"expected a finite integer, got {value!r}") from None
        if not math.isfinite(numeric) or not numeric.is_integer():
            raise ValueError(f"expected a finite integer, got {value!r}")
    if numeric < minimum or numeric > maximum:
        raise ValueError(f"integer outside Int64 range: {value!r}")
    return int(numeric)
