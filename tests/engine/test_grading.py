from ffdo.engine import grading


def test_grade_auction_pick_great_when_paid_well_under_baseline():
    assert grading.grade_auction_pick(baseline=100.0, amount=60) == "GREAT"


def test_grade_auction_pick_good_when_paid_slightly_under_baseline():
    assert grading.grade_auction_pick(baseline=100.0, amount=85) == "GOOD"


def test_grade_auction_pick_fair_when_paid_close_to_baseline():
    assert grading.grade_auction_pick(baseline=100.0, amount=105) == "FAIR"


def test_grade_auction_pick_poor_when_paid_well_over_baseline():
    assert grading.grade_auction_pick(baseline=100.0, amount=130) == "POOR"


def test_grade_auction_pick_ratio_boundaries_are_inclusive():
    assert grading.grade_auction_pick(baseline=100.0, amount=70) == "GREAT"
    assert grading.grade_auction_pick(baseline=100.0, amount=95) == "GOOD"
    assert grading.grade_auction_pick(baseline=100.0, amount=110) == "FAIR"


def test_grade_auction_pick_defaults_to_fair_with_no_baseline_signal():
    """A zero or negative baseline carries no fair-value signal -- grading
    it POOR or GREAT would fabricate a verdict the model has no basis for."""
    assert grading.grade_auction_pick(baseline=0.0, amount=5) == "FAIR"


def test_grade_snake_pick_great_at_or_below_5th_percentile():
    alternatives = list(range(99, -1, -1))  # 100 values, 99 down to 0
    assert grading.grade_snake_pick(picked_vor=100.0, alternative_vors=alternatives) == "GREAT"
    assert grading.grade_snake_pick(picked_vor=94.0, alternative_vors=alternatives) == "GREAT"  # exactly 5%


def test_grade_snake_pick_good_between_5_and_20_percent():
    alternatives = list(range(99, -1, -1))
    assert grading.grade_snake_pick(picked_vor=93.0, alternative_vors=alternatives) == "GOOD"
    assert grading.grade_snake_pick(picked_vor=79.0, alternative_vors=alternatives) == "GOOD"  # exactly 20%


def test_grade_snake_pick_fair_between_20_and_50_percent():
    alternatives = list(range(99, -1, -1))
    assert grading.grade_snake_pick(picked_vor=78.0, alternative_vors=alternatives) == "FAIR"
    assert grading.grade_snake_pick(picked_vor=49.0, alternative_vors=alternatives) == "FAIR"  # exactly 50%


def test_grade_snake_pick_poor_above_50_percent():
    alternatives = list(range(99, -1, -1))
    assert grading.grade_snake_pick(picked_vor=48.0, alternative_vors=alternatives) == "POOR"
    assert grading.grade_snake_pick(picked_vor=0.0, alternative_vors=alternatives) == "POOR"


def test_grade_snake_pick_defaults_to_fair_when_no_alternatives_remain():
    """Nothing with positive VOR was left on the board -- there's no reach
    to grade, so this isn't a POOR pick by default."""
    assert grading.grade_snake_pick(picked_vor=-5.0, alternative_vors=[]) == "FAIR"


def test_grade_snake_pick_defaults_to_fair_when_pool_is_thin():
    """A pool below SNAKE_MIN_POOL_SIZE (10) grades FAIR rather than running
    the percentile math -- late in a real draft the pool of still-relevant
    (VOR > 0) alternatives can shrink to a handful without ever emptying,
    and measuring a pick against only 3 survivors it structurally can't beat
    collapses to POOR almost automatically. This 3-element pool previously
    graded POOR under the old percentile-only logic; it now grades FAIR."""
    assert grading.grade_snake_pick(picked_vor=-5.0, alternative_vors=[10.0, 5.0, 1.0]) == "FAIR"


def test_grade_snake_pick_thin_pool_floor_overrides_what_would_otherwise_be_poor():
    """A 5-element pool where the picked player has the worst VOR of the six
    total (picked + 5 alternatives) would have graded POOR under the old
    percentile-only logic (beaten by 100% of a nonempty pool). Below
    SNAKE_MIN_POOL_SIZE, the thin-pool floor grades it FAIR instead."""
    assert grading.grade_snake_pick(
        picked_vor=0.0, alternative_vors=[50.0, 40.0, 30.0, 20.0, 10.0]) == "FAIR"
