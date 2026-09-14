from ffdo.domain.constants import INJURY_OUT_STATUSES, DYNASTY_AGE_CURVE


def test_injury_out_statuses_covers_the_known_hard_outs():
    assert INJURY_OUT_STATUSES == {"IR", "PUP", "Out", "Sus"}


def test_dynasty_age_curve_covers_the_core_offensive_positions():
    for position in ("QB", "RB", "WR", "TE"):
        assert position in DYNASTY_AGE_CURVE
        assert len(DYNASTY_AGE_CURVE[position]) > 0


def test_dynasty_age_curve_values_are_finite_and_plausible():
    for position, by_age in DYNASTY_AGE_CURVE.items():
        for age, delta in by_age.items():
            assert isinstance(age, int)
            assert 15 < age < 50   # a sanity bound, not a hard business rule
            assert -50.0 < delta < 50.0   # PPG deltas this large would indicate a scoring bug upstream


from ffdo.domain.constants import PICK_VALUE_CURVE


def test_pick_value_curve_every_round_has_a_round_avg_fallback():
    for rnd, tiers in PICK_VALUE_CURVE.items():
        assert "round_avg" in tiers, f"round {rnd} has no round_avg fallback"


def test_pick_value_curve_values_are_plausible_vor_magnitudes():
    for tiers in PICK_VALUE_CURVE.values():
        for key, val in tiers.items():
            if key == "exact":
                for v in val.values():
                    assert -100.0 < v < 400.0
            else:
                assert -100.0 < val < 400.0


def test_pick_value_curve_rounds_are_positive_integers():
    for rnd in PICK_VALUE_CURVE:
        assert isinstance(rnd, int) and rnd >= 1


from ffdo.domain.constants import FAAB_BID_CURVE


def test_faab_bid_curve_keys_are_ints():
    for bucket in FAAB_BID_CURVE:
        assert isinstance(bucket, int)


def test_faab_bid_curve_values_are_plausible_bid_fractions():
    for pct in FAAB_BID_CURVE.values():
        assert 0.0 <= pct < 5.0


def test_faab_bid_curve_is_not_empty():
    assert len(FAAB_BID_CURVE) > 0
