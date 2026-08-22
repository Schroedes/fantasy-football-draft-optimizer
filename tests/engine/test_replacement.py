from ffdo.domain.models import LeagueProfile
from ffdo.engine import replacement


def _league(roster_positions, num_teams=12):
    return LeagueProfile(league_id="x", season=2026, num_teams=num_teams,
                         roster_positions=tuple(roster_positions),
                         scoring_settings={}, budget=200)


def _pool():
    """40 RBs and 40 WRs on a clean descending scale, plus 20 QBs."""
    points, positions = {}, {}
    for i in range(40):
        points[f"rb{i}"] = 300.0 - i * 5
        positions[f"rb{i}"] = "RB"
        points[f"wr{i}"] = 290.0 - i * 5
        positions[f"wr{i}"] = "WR"
    for i in range(20):
        points[f"qb{i}"] = 400.0 - i * 10
        positions[f"qb{i}"] = "QB"
    return points, positions


def test_replacement_falls_as_starting_demand_rises():
    points, positions = _pool()
    shallow = replacement.replacement_levels(
        points, positions, _league(["QB", "RB", "WR", "BN"]))
    deep = replacement.replacement_levels(
        points, positions, _league(["QB", "RB", "RB", "RB", "WR", "BN"]))
    assert deep["RB"] < shallow["RB"]


def test_superflex_collapses_qb_replacement():
    points, positions = _pool()
    one_qb = replacement.replacement_levels(
        points, positions, _league(["QB", "RB", "RB", "WR", "WR", "BN"]))
    superflex = replacement.replacement_levels(
        points, positions,
        _league(["QB", "RB", "RB", "WR", "WR", "SUPER_FLEX", "BN"]))
    assert superflex["QB"] < one_qb["QB"]


def test_flex_demand_is_allocated_across_eligible_positions():
    points, positions = _pool()
    no_flex = replacement.replacement_levels(
        points, positions, _league(["RB", "WR", "BN"]))
    with_flex = replacement.replacement_levels(
        points, positions, _league(["RB", "WR", "FLEX", "BN"]))
    assert with_flex["RB"] <= no_flex["RB"]
    assert with_flex["WR"] <= no_flex["WR"]


def test_positions_absent_from_roster_get_no_level():
    points, positions = _pool()
    levels = replacement.replacement_levels(
        points, positions, _league(["QB", "RB", "WR", "BN"]))
    assert "TE" not in levels


def test_replacement_level_is_monotone_in_team_count():
    points, positions = _pool()
    small = replacement.replacement_levels(
        points, positions, _league(["QB", "RB", "WR", "BN"], num_teams=8))
    big = replacement.replacement_levels(
        points, positions, _league(["QB", "RB", "WR", "BN"], num_teams=12))
    assert big["RB"] < small["RB"]
