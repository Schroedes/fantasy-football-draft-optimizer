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
