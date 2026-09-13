import pytest

from ffdo.domain.constants import FAAB_BID_CURVE
from ffdo.engine import waiver_value


def test_exact_bucket_used_when_present():
    curve = {0: 0.05, 10: 0.15, 20: 0.30}
    result = waiver_value.suggested_bid(22.0, 100.0, curve)
    assert result == pytest.approx(30.0)  # bucket 20 -> 0.30 * 100


def test_falls_back_to_nearest_bucket_below_when_exact_bucket_missing():
    curve = {0: 0.05, 20: 0.30}
    result = waiver_value.suggested_bid(15.0, 100.0, curve)
    # bucket for 15.0 is 10 (missing) -- falls to bucket 0, not up to 20
    assert result == pytest.approx(5.0)


def test_never_extrapolates_upward_to_a_higher_bucket():
    curve = {0: 0.05}
    result = waiver_value.suggested_bid(999.0, 100.0, curve)
    # only bucket 0 exists and is <= bucket(999) -- uses it, does not
    # invent a value for an unseen high-VOR bucket
    assert result == pytest.approx(5.0)


def test_returns_zero_when_no_bucket_at_or_below_exists():
    curve = {50: 0.40}
    result = waiver_value.suggested_bid(10.0, 100.0, curve)
    assert result == 0.0


def test_empty_curve_returns_zero():
    result = waiver_value.suggested_bid(50.0, 100.0, {})
    assert result == 0.0


def test_custom_bucket_width_is_respected():
    curve = {0: 0.10, 5: 0.50}
    result = waiver_value.suggested_bid(7.0, 100.0, curve, bucket_width=5)
    assert result == pytest.approx(50.0)


def test_real_curve_higher_vor_gain_bucket_never_suggests_less_than_the_lowest_bucket():
    """Real-magnitude check against the actual fitted curve (Global
    Constraints: the curve-fitting-derived module needs at least one test
    against real data, not just synthetic fixtures -- the exact regression
    class that let bugs into #4's and #5's shipped formulas)."""
    if len(FAAB_BID_CURVE) < 2:
        pytest.skip("real curve has fewer than 2 populated buckets")
    lowest_bucket = min(FAAB_BID_CURVE)
    highest_bucket = max(FAAB_BID_CURVE)
    low_bid = waiver_value.suggested_bid(float(lowest_bucket), 100.0, FAAB_BID_CURVE)
    high_bid = waiver_value.suggested_bid(float(highest_bucket) + 5.0, 100.0, FAAB_BID_CURVE)
    assert high_bid >= low_bid
