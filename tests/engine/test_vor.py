from ffdo.domain.models import LeagueProfile, PlayerProfile
from ffdo.engine import scoring, vor


def _profiles(spec):
    return {pid: PlayerProfile(player_id=pid, first_name="P", last_name=pid,
                               position=pos, team="X", age=25, years_exp=3,
                               injury_status=None, active=True)
            for pid, pos in spec.items()}


def _league(n=2):
    return LeagueProfile(league_id="x", season=2026, num_teams=n,
                         roster_positions=("RB", "WR", "BN"),
                         scoring_settings={}, budget=200)


def test_vor_is_points_above_replacement():
    points = {f"rb{i}": 200.0 - 10 * i for i in range(6)}
    profiles = _profiles({f"rb{i}": "RB" for i in range(6)})
    points.update({f"wr{i}": 150.0 - 10 * i for i in range(6)})
    profiles.update(_profiles({f"wr{i}": "WR" for i in range(6)}))

    valued = vor.compute(points, profiles, _league())
    # 2 teams x 1 RB slot => replacement RB is the 3rd best (180.0)
    assert valued["rb0"].vor == 200.0 - 180.0


def test_adjustments_are_applied_and_recorded():
    points = {"rb0": 200.0, "rb1": 190.0, "rb2": 180.0,
              "wr0": 150.0, "wr1": 140.0, "wr2": 130.0}
    profiles = _profiles({"rb0": "RB", "rb1": "RB", "rb2": "RB",
                          "wr0": "WR", "wr1": "WR", "wr2": "WR"})
    valued = vor.compute(points, profiles, _league(),
                         adjustments={"rb0": {"durability": -12.0}})
    assert valued["rb0"].adjusted_points == 188.0
    assert valued["rb0"].projected_points == 200.0
    assert valued["rb0"].adjustments["durability"] == -12.0


def test_tiers_break_on_large_gaps():
    points = {"a": 100.0, "b": 99.0, "c": 98.0,  # tier 1
              "d": 60.0, "e": 59.0}              # tier 2 after a big gap
    profiles = _profiles({k: "RB" for k in points})
    valued = vor.assign_tiers(vor.compute(points, profiles, _league(n=1)))
    assert valued["a"].tier == valued["b"].tier == valued["c"].tier
    assert valued["d"].tier == valued["e"].tier
    assert valued["d"].tier > valued["a"].tier


def test_players_at_positions_absent_from_the_roster_are_excluded():
    """A position with no dedicated slot and no FLEX-eligibility (e.g. a
    kicker in a league that starts no K) has no replacement level to compare
    against. Previously `vor.compute` defaulted the missing level to 0.0,
    which turned the player's raw point total into his VOR -- making an
    unrostered position look like a windfall bargain instead of excluding
    it. This is the root cause `app.py`'s SKILL_POSITIONS filter patched at
    one call site; the fix belongs here so it holds regardless of caller.
    """
    points = {"rb0": 200.0, "rb1": 190.0, "rb2": 180.0, "k0": 150.0}
    profiles = _profiles({"rb0": "RB", "rb1": "RB", "rb2": "RB", "k0": "K"})
    valued = vor.compute(points, profiles, _league(n=1))
    assert "k0" not in valued
    assert "rb0" in valued


def test_tiers_are_assigned_within_position_not_across():
    points = {"rb0": 300.0, "rb1": 299.0, "wr0": 100.0, "wr1": 99.0}
    profiles = _profiles({"rb0": "RB", "rb1": "RB", "wr0": "WR", "wr1": "WR"})
    valued = vor.assign_tiers(vor.compute(points, profiles, _league(n=1)))
    assert valued["rb0"].tier == 1
    assert valued["wr0"].tier == 1


def test_vor_reflects_the_leagues_scoring_settings():
    """Different scoring_settings must produce a different VOR for the same
    raw stat line -- ffdo.engine.scoring.score_stats and ffdo.engine.vor.compute
    take scoring_settings/league as parameters rather than hardcoding one
    league's rules, so switching the connected league (see ffdo.ingest.connect)
    must actually change valuations, not just round-trip the settings."""
    stats = {
        "star": {"rec": 80.0, "rec_yd": 600.0, "rush_yd": 400.0, "rush_td": 4},
        "replacement": {"rec": 20.0, "rec_yd": 150.0, "rush_yd": 300.0, "rush_td": 1},
    }
    profiles = _profiles({"star": "RB", "replacement": "RB"})
    league = LeagueProfile(league_id="x", season=2026, num_teams=1,
                           roster_positions=("RB",), scoring_settings={}, budget=200)

    def star_vor(scoring_settings):
        points = {pid: scoring.score_stats(s, scoring_settings) for pid, s in stats.items()}
        return vor.compute(points, profiles, league)["star"].vor

    full_ppr_vor = star_vor({"rec": 1.0, "rec_yd": 0.1, "rush_yd": 0.1, "rush_td": 6})
    standard_vor = star_vor({"rec": 0.0, "rec_yd": 0.1, "rush_yd": 0.1, "rush_td": 6})

    assert full_ppr_vor != standard_vor
    assert full_ppr_vor == 133.0  # 204 - 71
    assert standard_vor == 73.0   # 124 - 51
