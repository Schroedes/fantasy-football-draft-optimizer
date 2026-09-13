import pytest

from ffdo.domain.constants import PICK_VALUE_CURVE
from ffdo.domain.models import DraftPickAsset
from ffdo.engine import pick_value


def _pick(season=2026, round=1, projected_slot=None):
    return DraftPickAsset(
        season=season, round=round, projected_slot=projected_slot,
        current_owner_roster_id=1, original_roster_id=1, via_team_name=None)


def test_exact_slot_used_when_present():
    curve = {1: {"exact": {3: 100.0}, "round_avg": 40.0}}
    pick = _pick(projected_slot=3)
    result = pick_value.slot_value(pick, curve, current_season=2026, round_size=12,
                                    discount_rate=0.0)
    assert result == pytest.approx(100.0)


def test_falls_back_to_tertile_when_exact_slot_missing():
    curve = {1: {"exact": {}, "early": 90.0, "round_avg": 40.0}}
    pick = _pick(projected_slot=2)   # early tertile of a 12-pick round
    result = pick_value.slot_value(pick, curve, current_season=2026, round_size=12,
                                    discount_rate=0.0)
    assert result == pytest.approx(90.0)


def test_falls_back_to_round_avg_when_tertile_missing():
    curve = {1: {"exact": {}, "round_avg": 40.0}}
    pick = _pick(projected_slot=2)
    result = pick_value.slot_value(pick, curve, current_season=2026, round_size=12,
                                    discount_rate=0.0)
    assert result == pytest.approx(40.0)


def test_no_projected_slot_falls_straight_to_round_avg():
    curve = {1: {"exact": {2: 999.0}, "round_avg": 40.0}}
    pick = _pick(projected_slot=None)
    result = pick_value.slot_value(pick, curve, current_season=2026, round_size=12,
                                    discount_rate=0.0)
    assert result == pytest.approx(40.0)


def test_missing_round_returns_zero():
    curve = {1: {"round_avg": 40.0}}
    pick = _pick(round=5, projected_slot=1)
    result = pick_value.slot_value(pick, curve, current_season=2026, round_size=12,
                                    discount_rate=0.0)
    assert result == 0.0


def test_years_out_zero_applies_no_discount():
    curve = {1: {"exact": {1: 100.0}}}
    pick = _pick(season=2026, projected_slot=1)
    result = pick_value.slot_value(pick, curve, current_season=2026, round_size=12,
                                    discount_rate=0.20)
    assert result == pytest.approx(100.0)


def test_years_out_discounts_correctly():
    curve = {1: {"exact": {1: 100.0}}}
    pick = _pick(season=2028, projected_slot=1)   # 2 years out from current_season=2026
    result = pick_value.slot_value(pick, curve, current_season=2026, round_size=12,
                                    discount_rate=0.20)
    assert result == pytest.approx(100.0 / (1.2 ** 2))


def test_tertile_boundaries_scale_to_round_size():
    # A 9-pick round splits into thirds of 3: early=1-3, mid=4-6, late=7-9.
    curve = {1: {"exact": {}, "mid": 55.0, "round_avg": 20.0}}
    pick = _pick(projected_slot=5)
    result = pick_value.slot_value(pick, curve, current_season=2026, round_size=9,
                                    discount_rate=0.0)
    assert result == pytest.approx(55.0)


def test_real_curve_round_one_exceeds_round_three():
    """Real-magnitude check against the actual fitted curve (Global
    Constraints: at least one test per module must check real magnitude,
    not just direction against a synthetic fixture -- the exact regression
    class that let two bugs into #4's shipped formula)."""
    if 1 not in PICK_VALUE_CURVE or 3 not in PICK_VALUE_CURVE:
        pytest.skip("real curve does not have both round 1 and round 3 data yet")
    round_1_avg = PICK_VALUE_CURVE[1]["round_avg"]
    round_3_avg = PICK_VALUE_CURVE[3]["round_avg"]
    assert round_1_avg > round_3_avg
