from ffdo.engine import scarcity


def test_positional_cliff_measures_gap_below_replacement():
    ranked = {"RB": [(30.0, "a"), (20.0, "b"), (15.0, "c"),
                     (10.0, "d"), (5.0, "e"), (2.0, "f")]}
    levels = {"RB": 15.0}
    cliff = scarcity.positional_cliff(ranked, levels)
    # replacement is "c" (15.0) at idx 2; next 3 below are 10.0/5.0/2.0, mean 5.6667
    assert cliff["RB"] == 15.0 - (10.0 + 5.0 + 2.0) / 3


def test_positional_cliff_thin_pool_uses_fewer_than_depth():
    ranked = {"TE": [(10.0, "a"), (5.0, "b")]}
    levels = {"TE": 5.0}
    cliff = scarcity.positional_cliff(ranked, levels)
    assert cliff["TE"] == 5.0 - 5.0  # only "b" itself is at/below level, nothing below it


def test_positional_cliff_nothing_below_replacement_is_zero():
    ranked = {"K": [(10.0, "a")]}
    levels = {"K": 10.0}
    cliff = scarcity.positional_cliff(ranked, levels)
    assert cliff["K"] == 0.0


def test_positional_cliff_ties_at_replacement_are_never_counted_as_below():
    """Regression: a naive positional slice after finding the first index
    <= level can sweep in ANOTHER player tied at the same value as
    'below' -- e.g. here, three players (c, d, e) are all tied at exactly
    the replacement level (15.0). None of them may ever count as 'below'
    the replacement, since none is actually worth less than it."""
    ranked = {"TE": [(30.0, "a"), (20.0, "b"), (15.0, "c"), (15.0, "d"),
                     (15.0, "e"), (10.0, "f"), (5.0, "g")]}
    levels = {"TE": 15.0}
    cliff = scarcity.positional_cliff(ranked, levels)
    # Only f (10.0) and g (5.0) are genuinely below 15.0 -- CLIFF_DEPTH=3
    # would want a third, but there isn't one, so this averages over 2.
    assert cliff["TE"] == 15.0 - (10.0 + 5.0) / 2


def test_scarcity_multiplier_zero_strength_is_a_no_op():
    cliff = {"RB": 20.0, "WR": 4.0}
    result = scarcity.scarcity_multiplier(cliff, 0.0)
    assert result == {"RB": 1.0, "WR": 1.0}


def test_scarcity_multiplier_scales_by_relative_cliff():
    cliff = {"RB": 20.0, "WR": 4.0}
    result = scarcity.scarcity_multiplier(cliff, 1.0)
    assert result["RB"] == 1.0 + 1.0 * (20.0 / 20.0)
    assert result["WR"] == 1.0 + 1.0 * (4.0 / 20.0)


def test_scarcity_multiplier_all_zero_cliffs_returns_ones():
    cliff = {"RB": 0.0, "WR": 0.0}
    result = scarcity.scarcity_multiplier(cliff, 2.0)
    assert result == {"RB": 1.0, "WR": 1.0}


def test_scarcity_multiplier_empty_cliff_returns_empty():
    assert scarcity.scarcity_multiplier({}, 1.0) == {}
