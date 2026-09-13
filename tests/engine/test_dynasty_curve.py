from ffdo.engine.dynasty_curve import multiplier


def test_young_rb_beats_old_rb():
    assert multiplier("RB", 24, 3) > multiplier("RB", 30, 8)


def test_prime_wr_near_one():
    assert 0.95 <= multiplier("WR", 26, 4) <= 1.15


def test_rookie_is_nudged_down():
    assert multiplier("WR", 22, 0) < multiplier("WR", 22, 3)


def test_none_age_is_treated_as_peak_no_penalty():
    assert multiplier("RB", None, 3) >= 1.0


def test_kicker_and_defense_are_age_agnostic():
    assert multiplier("K", 39, 15) == 1.0
    assert multiplier("DEF", None, None) == 1.0


def test_always_clamped_to_band():
    for pos in ("QB", "RB", "WR", "TE"):
        for age in range(19, 45):
            m = multiplier(pos, age, 5)
            assert 0.55 <= m <= 1.15
