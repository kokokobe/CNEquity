from decimal import Decimal

import numpy as np
import pytest

from cnequity.adapters.numeric import finite_int64


def test_finite_int64_rejects_non_integral_nonfinite_and_overflow_values():
    assert finite_int64(12.0) == 12
    with pytest.raises(ValueError):
        finite_int64(12.5)
    with pytest.raises(ValueError):
        finite_int64(float("inf"))
    with pytest.raises(ValueError):
        finite_int64(1e300)


def test_finite_int64_supports_domain_lower_bound():
    with pytest.raises(ValueError):
        finite_int64(-1, minimum=0)


def test_finite_int64_retains_existing_float_semantics():
    assert finite_int64(float(2**53 + 1)) == 2**53
    with pytest.raises(ValueError, match="outside Int64 range"):
        finite_int64(float(2**63))


@pytest.mark.parametrize("value", [2**53 + 1, -(2**53 + 1), 2**63 - 1, -(2**63)])
@pytest.mark.parametrize("representation", [int, str, Decimal, np.int64])
def test_finite_int64_preserves_exact_integer_values(value, representation):
    assert finite_int64(representation(value)) == value


@pytest.mark.parametrize("value", [2**63, -(2**63) - 1])
@pytest.mark.parametrize("representation", [int, str, Decimal])
def test_finite_int64_rejects_integer_overflow_at_either_boundary(value, representation):
    with pytest.raises(ValueError, match="outside Int64 range"):
        finite_int64(representation(value))


@pytest.mark.parametrize("value", ["9007199254740993.0", "9.007199254740993e15"])
def test_finite_int64_accepts_integral_decimal_and_exponent_text_exactly(value):
    assert finite_int64(value) == 2**53 + 1


@pytest.mark.parametrize(
    "value",
    [
        "9007199254740993.1",
        Decimal("9007199254740993.1"),
        "1e-400",
        Decimal("1e-400"),
        "NaN",
        Decimal("NaN"),
        "Infinity",
        Decimal("Infinity"),
        "bad",
    ],
)
def test_finite_int64_rejects_nonintegral_or_nonfinite_decimal_values(value):
    with pytest.raises(ValueError, match="expected a finite integer"):
        finite_int64(value)
